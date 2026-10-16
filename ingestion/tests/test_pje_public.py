import gzip
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from bpc_ingestion import api
from bpc_ingestion.models import Base, Coleta, DocumentoProcesso, DocumentoPublico, Processo
from bpc_ingestion.pje_public import (HOME, HOST, DETAIL_PATH, DOC_PATH, collect,
    detail_documents, official_url, parse_document, result_detail, search_form)
from bpc_ingestion.postgres_store import PostgresStore
from bpc_ingestion.public_sources import SourceBlocked, store_document

NUMBER = "1053078-37.2022.4.01.3400"
DETAIL = f"https://{HOST}{DETAIL_PATH}?ca=abc123"
DOC = f"https://{HOST}{DOC_PATH}?ca=abc123&idProcessoDoc=12345"


def form(active=False):
    return f"""<form id='fPP' action='{HOME}'>
    <input name='fPP:numProcesso-inputNumeroProcesso'><input name='javax.faces.ViewState' value='state'>
    <input type='button' name='fPP:searchProcessos'>
    </form><script>
    function executarReCaptcha() {{ if ({'true' if active else 'false'}) {{ hcaptcha.execute(); return false; }} executarPesquisa(); }}
    executarPesquisa=function(){{ A4J.AJAX.Submit('fPP',null,{{'parameters':{{'fPP:published':'fPP:published'}}}});}};
    </script>""".encode()


def result(number=NUMBER):
    return f"""<table id='fPP:processosTable'><tbody><tr><td>{number}</td>
    <td><a onclick="openPopUp('Consulta','{DETAIL}')">detalhe</a></td></tr></tbody></table>""".encode()


def detail(number=NUMBER, kind="Sentença"):
    return f"""<meta charset='utf-8'><div>Número Processo: {number}</div>
    <table id='x:processoDocumentoGridTab'><tbody><tr><td>08/10/2026 10:00:00 - {kind} ({kind})</td>
    <td><a title='Visualizar' onclick="openPopUp('Documento','{DOC}')">abrir</a></td></tr></tbody></table>""".encode()


def document(number=NUMBER, doc_id="12345"):
    return f"""<meta charset='utf-8'><div class='folha'><p>PROCESSO: {number}</p>
    <script>segredo</script><style>estilo</style><p>SENTENÇA: fundamento e dispositivo.</p>
    <p>Precedente 0000001-00.2020.4.01.3400.</p><p>ID do documento: {doc_id}</p>
    <form>Baixar PDF</form></div>""".encode()


class PjePublicTest(unittest.TestCase):
    def test_published_ajax_not_button(self):
        target, data = search_form(form(), NUMBER)
        self.assertEqual(target, HOME)
        self.assertEqual(dict(data)["fPP:published"], "fPP:published")
        self.assertNotIn("fPP:searchProcessos", dict(data))
        self.assertEqual(dict(data)["fPP:numProcesso-inputNumeroProcesso"], NUMBER)

    def test_active_or_unknown_captcha_stops(self):
        for body in (form(True), form().replace(b"if (false)", b"if (enabled)"),
                     form() + b"<div class='h-captcha'></div>"):
            with self.assertRaises(SourceBlocked):
                search_form(body, NUMBER)

    def test_search_form_rejects_offhost_or_arbitrary_path_parameters(self):
        for target in (HOME.replace(HOST, "example.com"), HOME + ";other/private", HOME + "?credential=x"):
            with self.assertRaises(ValueError):
                search_form(form().replace(HOME.encode(), target.encode()), NUMBER)

    def test_urls_reject_offhost_duplicates_extra_parameters_and_wrong_routes(self):
        for url in (DOC.replace(HOST, "example.com"), DOC + "&idProcessoDoc=2", DOC + "&extra=x",
                    DOC + "#fragment", DOC.replace("https://", "http://"), DOC.replace(DOC_PATH, DETAIL_PATH)):
            with self.assertRaises(ValueError):
                official_url(url, DOC_PATH, {"ca", "idProcessoDoc"})

    def test_result_identity_and_explicit_empty(self):
        self.assertEqual(result_detail(result(), NUMBER), DETAIL)
        self.assertIsNone(result_detail(b"0 resultados encontrados", NUMBER))
        for body in (b"200 OK", result("0000001-00.2020.4.01.3400")):
            with self.assertRaises(ValueError):
                result_detail(body, NUMBER)

    def test_detail_requires_labeled_identity(self):
        self.assertEqual(detail_documents(detail(), NUMBER), [("12345", "Sentença", DOC)])
        for body in (detail("0000001-00.2020.4.01.3400"), detail().replace("Número Processo".encode(), b"Citation")):
            with self.assertRaises(ValueError):
                detail_documents(body, NUMBER)

    def test_only_decision_descriptors_not_petitions_or_certificates(self):
        self.assertEqual(detail_documents(detail(kind="Petição sobre Sentença"), NUMBER), [])
        self.assertEqual(detail_documents(detail(kind="Certidão de trânsito em julgado"), NUMBER), [])
        self.assertEqual(len(detail_documents(detail(kind="Despacho"), NUMBER)), 1)

    def test_document_identity_not_precedent_and_no_ui(self):
        doc = parse_document(document(), NUMBER, "12345", "Sentença", DOC)
        self.assertEqual(doc["numeros_cnj"], [NUMBER])
        self.assertNotIn("segredo", doc["texto"])
        self.assertNotIn("Baixar PDF", doc["texto"])
        self.assertIsNone(doc["data_decisao"])
        self.assertEqual(doc["payload"]["revisao_privacidade"], "pendente")
        for label in ("PROCESSO Nº", "Processo nº.:", "PROCESSO N."):
            numbered = document().replace(b"PROCESSO:", label.encode())
            self.assertEqual(parse_document(numbered, NUMBER, "12345", "Sentença", DOC)["numeros_cnj"], [NUMBER])

    def test_document_without_header_or_mismatched_id_rejected(self):
        for body in (document(doc_id="999"), document("0000001-00.2020.4.01.3400"),
                     document().replace(b"PROCESSO:", b"Referencia:")):
            with self.assertRaises(ValueError):
                parse_document(body, NUMBER, "12345", "Sentença", DOC)

    def test_isolated_cnj_requires_exact_published_header_layout(self):
        old = f"<p>PROCESSO: {NUMBER}</p>".encode()
        new = ("<p>Juizado Especial Cível e Criminal Adjunto à Vara Federal</p>"
               "<p>Subseção Judiciária de Formosa-GO</p>"
               f"<div><span>{NUMBER}</span></div>"
               "<p>PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)</p>").encode()
        body = document().replace(old, new)
        self.assertEqual(parse_document(body, NUMBER, "12345", "Decisão", DOC)["numeros_cnj"], [NUMBER])
        citation = body.replace(b"<script>", b"<p>AUTOR: pessoa de teste</p><p>Pedido anterior (processo n\xc2\xba 0000001-00.2020.4.01.3400).</p><script>")
        self.assertEqual(parse_document(citation, NUMBER, "12345", "Decisão", DOC)["numeros_cnj"], [NUMBER])
        # Cabeçalho errado não pode ser resgatado por referência ao número esperado no corpo.
        wrong_header = body.replace(NUMBER.encode(), b"0000001-00.2020.4.01.3400")
        wrong_header = wrong_header.replace(b"<script>", f"<p>AUTOR: pessoa de teste</p><p>PROCESSO: {NUMBER}</p><script>".encode())
        with self.assertRaises(ValueError):
            parse_document(wrong_header, NUMBER, "12345", "Decisão", DOC)
        execution = body.replace("PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)".encode(),
                                 "CUMPRIMENTO DE SENTENÇA CONTRA A FAZENDA PÚBLICA (12078)".encode())
        self.assertEqual(parse_document(execution, NUMBER, "12345", "Decisão", DOC)["numeros_cnj"], [NUMBER])
        ms = body.replace("Juizado Especial Cível e Criminal Adjunto à Vara Federal".encode(),
                          "Vara Federal Cível e Criminal da SSJ de Formosa-GO".encode()).replace(
                          "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)".encode(), "MANDADO DE SEGURANÇA CÍVEL (120)".encode())
        self.assertEqual(parse_document(ms, NUMBER, "12345", "Decisão", DOC)["numeros_cnj"], [NUMBER])
        for changed in (body.replace("Subseção Judiciária".encode(), b"Citado em precedente"),
                        body.replace(b"(436)", b"(999)"), body.replace(NUMBER.encode(), b"0000001-00.2020.4.01.3400")):
            with self.assertRaises(ValueError):
                parse_document(changed, NUMBER, "12345", "Decisão", DOC)

    def test_appeal_header_requires_electronic_court_label_and_expected_number(self):
        old = f"<p>PROCESSO: {NUMBER}</p>".encode()
        new = ("<p>Processo Judicial Eletrônico</p>"
               f"<p>RECURSO INOMINADO CÍVEL (460) Nº {NUMBER}</p>").encode()
        body = document().replace(old, new)
        self.assertEqual(parse_document(body, NUMBER, "12345", "Acórdão", DOC)["numeros_cnj"], [NUMBER])
        sjdf = body.replace("<p>RECURSO INOMINADO CÍVEL (460) Nº".encode(),
            ("<p>Turmas Recursais dos Juizados Especiais Federais da SJDF</p>"
             "<p>3ª Relatoria da 1ª Turma Recursal da SJDF</p>"
             "<p>RECURSO INOMINADO CÍVEL (460) n.").encode())
        self.assertEqual(parse_document(sjdf, NUMBER, "12345", "Acórdão", DOC)["numeros_cnj"], [NUMBER])
        for changed in (body.replace("Processo Judicial Eletrônico".encode(), b"Precedente"),
                        body.replace(NUMBER.encode(), b"0000001-00.2020.4.01.3400")):
            with self.assertRaises(ValueError):
                parse_document(changed, NUMBER, "12345", "Acórdão", DOC)

    def test_isolated_number_after_section_and_jef_before_parties(self):
        new = ("<p>Seção Judiciária do Tocantins</p>"
               "<p>3ª Vara Federal de Juizado Especial Cível da SJTO</p>"
               f"<p>{NUMBER}</p><p>AUTOR: pessoa de teste</p>").encode()
        body = document().replace(f"<p>PROCESSO: {NUMBER}</p>".encode(), new)
        self.assertEqual(parse_document(body, NUMBER, "12345", "Despacho", DOC)["numeros_cnj"], [NUMBER])
        for changed in (body.replace("Seção Judiciária".encode(), b"Precedente da"),
                        body.replace(b"AUTOR:", b"CITACAO:")):
            with self.assertRaises(ValueError):
                parse_document(changed, NUMBER, "12345", "Despacho", DOC)

    def test_luziania_number_after_court_and_sentence_heading(self):
        lines = ["PODER JUDICIÁRIO", "JUSTIÇA FEDERAL", "Seção Judiciária do Estado de Goiás",
                 "JUIZADO ESPECIAL FEDERAL - SUBSEÇÃO JUDICIÁRIA DE LUZIÂNIA", 'SENTENÇA TIPO "C"', NUMBER]
        new = ''.join(f'<p>{line}</p>' for line in lines).encode()
        body = document().replace(f"<p>PROCESSO: {NUMBER}</p>".encode(), new)
        self.assertEqual(parse_document(body, NUMBER, "12345", "Sentença", DOC)["numeros_cnj"], [NUMBER])
        with self.assertRaises(ValueError):
            parse_document(body.replace(b'PODER JUDICI', b'PRECEDENTE JUDICI'), NUMBER, "12345", "Sentença", DOC)

    def test_formosa_branded_header(self):
        lines = ["PODER JUDICIÁRIO", "JUSTIÇA FEDERAL", "Subseção Judiciária de Formosa-GO",
                 "Juizado Especial Cível e Criminal Adjunto à Vara Federal da SSJ de Formosa-GO", NUMBER]
        body = document().replace(f"<p>PROCESSO: {NUMBER}</p>".encode(),
                                  ''.join(f'<p>{line}</p>' for line in lines).encode())
        self.assertEqual(parse_document(body, NUMBER, "12345", "Decisão", DOC)["numeros_cnj"], [NUMBER])
        with self.assertRaises(ValueError):
            parse_document(body.replace(b'PODER JUDICI', b'PRECEDENTE JUDICI'), NUMBER, "12345", "Decisão", DOC)

    def test_composite_appeal_institutional_block_not_loose_citation(self):
        block = ("<p>JUSTIÇA FEDERAL</p><p>SEÇÃO JUDICIÁRIA DO ESTADO DE GOIÁS</p>"
                 "<p>PRIMEIRA TURMA RECURSAL DOS JUIZADOS ESPECIAIS FEDERAIS</p>"
                 "<p>GABINETE DA 3ª RELATORIA</p>"
                 f"<p><strong>RECURSO JEF Nº {NUMBER}</strong></p>")
        body = document().replace(f"<p>PROCESSO: {NUMBER}</p>".encode(),
                                  ("<p>Texto do voto " + "x " * 2000 + "</p>" + block).encode())
        self.assertEqual(parse_document(body, NUMBER, "12345", "Acórdão", DOC)["numeros_cnj"], [NUMBER])
        for changed in (body.replace(b"<strong>", b"<span>").replace(b"</strong>", b"</span>"),
                        body.replace("GABINETE DA 3ª RELATORIA".encode(), b"CITACAO"),
                        body.replace(block.encode(), ("<blockquote>" + block + "</blockquote>").encode()),
                        body.replace(block.encode(), (block + block).encode())):
            with self.assertRaises(ValueError):
                parse_document(changed, NUMBER, "12345", "Acórdão", DOC)

    def test_panel_retains_text_and_links(self):
        document_json = parse_document(document(), NUMBER, "12345", "Sentença", DOC)
        obj = DocumentoPublico(**document_json)
        obj.id, obj.hash_conteudo = 1, "a" * 64
        rendered = api.public_document_json(obj)
        for field in ("texto", "ementa", "decisao", "url_origem", "recurso_url"):
            self.assertIsNone(rendered[field])
        self.assertEqual(rendered["revisao_privacidade"], "pendente")
        self.assertIn('value="pje_trf1"', (api.STATIC_DIR / "documentos.html").read_text(encoding="utf-8"))

    def test_collect_bronze_resume_dedup_and_failure_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PostgresStore(f"sqlite:///{Path(tmp) / 'test.sqlite'}")
            Base.metadata.create_all(store.engine)
            with store.Session.begin() as session:
                session.add(Processo(numero_processo=NUMBER))
            responses = [form(), result(), detail(), document()]
            with patch("bpc_ingestion.pje_public.PublicHttp.fetch", side_effect=responses) as fetch:
                summary = collect(store, [NUMBER], Path(tmp) / "raw", 10, 25)
                self.assertEqual(fetch.call_count, 4)
            self.assertEqual(summary["documentos_novos"], 1)
            for entry in summary["resultados"][0]["bronze"]:
                self.assertTrue(gzip.decompress(Path(entry["arquivo"]).read_bytes()))
            with patch("bpc_ingestion.pje_public.PublicHttp.fetch") as fetch:
                self.assertEqual(collect(store, [NUMBER], Path(tmp) / "raw", 10, 25)["consultados"], 0)
                fetch.assert_not_called()
            with store.Session.begin() as session:
                doc = parse_document(document(), NUMBER, "12345", "Sentença", DOC)
                self.assertFalse(store_document(session, doc, summary["resultados"][0]["coleta"], Path("unused")))
                self.assertEqual(session.scalar(select(func.count()).select_from(DocumentoProcesso)), 1)
                # Failed prior collection may be retried, without overwriting successful versions.
                session.get(Coleta, uuid.UUID(summary["resultados"][0]["coleta"])).status = "falhou"
            with patch("bpc_ingestion.pje_public.PublicHttp.fetch", side_effect=[form(), b"layout inesperado"]):
                rejected = collect(store, [NUMBER], Path(tmp) / "raw", 10, 25)
            self.assertTrue(rejected["interrompida"])
            self.assertEqual(rejected["resultados"][0]["status"], "falhou")
            with store.Session() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(DocumentoPublico)), 1)
            store.close()

    def test_misrouted_document_stops_batch_and_preserves_only_verified_document(self):
        other = "0000001-00.2020.4.01.3400"
        second_url = DOC.replace("12345", "56789")
        extra_row = ("<tr><td>08/10/2026 10:00:00 - Sentença (Sentença)</td>"
                     f"<td><a title='Visualizar' onclick=\"openPopUp('Documento','{second_url}')\">abrir</a></td></tr>")
        page = detail().replace(b"</tbody>", extra_row.encode() + b"</tbody>")
        with tempfile.TemporaryDirectory() as tmp:
            store = PostgresStore(f"sqlite:///{Path(tmp) / 'test.sqlite'}")
            Base.metadata.create_all(store.engine)
            with store.Session.begin() as session:
                session.add_all([Processo(numero_processo=NUMBER), Processo(numero_processo=other)])
            with patch("bpc_ingestion.pje_public.PublicHttp.fetch", side_effect=[
                    form(), result(), page, document(), document(other, "56789")]) as fetch:
                rejected = collect(store, [NUMBER, other], Path(tmp) / "raw", 10, 25)
            self.assertEqual(fetch.call_count, 5)
            self.assertTrue(rejected["interrompida"])
            self.assertEqual(rejected["consultados"], 1)
            self.assertEqual(rejected["documentos_novos"], 1)
            self.assertEqual(rejected["resultados"][0]["status"], "falhou")
            with store.Session() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(DocumentoPublico)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(DocumentoProcesso)), 1)
                self.assertEqual(session.scalar(select(func.count()).select_from(Coleta)), 1)
                kept = session.scalar(select(DocumentoPublico))
                self.assertEqual(kept.documento_id, "12345")
                run = session.scalar(select(Coleta))
                self.assertEqual(run.registros, 1)
                self.assertIn("CNJ do cabeçalho diverge", run.erro)
            store.close()


if __name__ == "__main__":
    unittest.main()
