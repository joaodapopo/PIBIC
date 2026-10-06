import gzip
import io
import json
import tempfile
import unittest
import uuid
import zipfile
from unittest.mock import Mock
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from bpc_ingestion import api
from bpc_ingestion.database import make_engine
from bpc_ingestion.models import Base, Coleta, DocumentoProcesso, DocumentoPublico, Processo
from bpc_ingestion.public_sources import (ArchiveClient, CjfClient, SourceBlocked, StjClient, bpc_text,
    check_blocked, cnj_number, preserve_raw, public_date, stj_document, store_document)


class PublicSourcesTest(unittest.TestCase):
    def test_stj_schema_and_no_link_from_short_number(self):
        item = {"id": "1459483", "numeroProcesso": "3126923", "ementa": "Benefício assistencial BPC",
                "decisao": "Recurso não provido", "dataPublicacao": "DJEN DATA:18/08/2026"}
        result = stj_document(item, "https://dadosabertos.web.stj.jus.br/a.json")
        self.assertTrue(bpc_text(item))
        self.assertEqual(result["numeros_cnj"], [])
        self.assertEqual(result["data_publicacao"], date(2026, 8, 18))
        self.assertEqual(result["payload"], item)
        self.assertFalse(bpc_text({"ementa": "Aposentadoria rural"}))
        with self.assertRaises(ValueError):
            stj_document({}, "https://example.test")

    def test_cjf_observed_html_layout(self):
        html = b'''<div id="item_resultado-12"><div class="ui-outputpanel"><tr><td>Numero</td></tr>
        <tr><td>1053078-37.2022.4.01.3400 10530783720224013400</td></tr></div>
        <div class="ui-outputpanel"><tr><td>Ementa</td></tr><tr><td>BPC LOAS</td></tr></div></div>'''
        rows = CjfClient.documents(html, "JEF1")
        self.assertEqual(rows[0]["ementa"], "BPC LOAS")
        self.assertEqual(rows[0]["documento_id"], "12")
        self.assertEqual(rows[0]["numeros_cnj"], ["1053078-37.2022.4.01.3400"])
        self.assertEqual(CjfClient.documents(b"0 Documento(s) encontrado(s)", "TRF1"), [])
        with self.assertRaises(ValueError):
            CjfClient.documents(b"Unexpected error", "TRF1")

    def test_public_dates_numbers_and_blocking(self):
        self.assertIsNone(cnj_number("AREsp 3126923"))
        self.assertEqual(cnj_number("10530783720224013400"), "1053078-37.2022.4.01.3400")
        self.assertIsNone(public_date("31/99/2026"))
        for text in ('<script src="/cdn-cgi/challenge-platform/x"></script>', 'class="g-recaptcha"'):
            with self.assertRaises(SourceBlocked):
                check_blocked(text)
        # Biblioteca global de CAPTCHA não significa desafio exigido no formulário.
        check_blocked('<script src="https://google.com/recaptcha/api.js"></script>')

    def test_raw_preserves_exact_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            body = b'[ {"id":1} ]\n'
            path = preserve_raw(body, Path(work), "stj", "run", "json")
            with gzip.open(path, "rb") as source:
                self.assertEqual(source.read(), body)

    def test_resource_selection(self):
        package = {"resources": [{"format": "JSON", "url": "https://example.test/20260731.json"},
                                 {"format": "ZIP", "url": "https://example.test/history.zip"},
                                 {"format": "JSON", "url": "https://example.test/20260831.json"}]}
        self.assertTrue(StjClient.recent_json(package, 1)[0]["url"].endswith("20260831.json"))
        self.assertEqual(len(StjClient.historical_zip(package)), 1)

    def test_historical_zip_reads_members_without_extracting_paths(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("../../outside.json", '[{"id":1,"ementa":"LOAS"}]')
            bundle.writestr("20220508.json", '[{"id":2,"ementa":"BPC"}]')
            bundle.writestr("readme.txt", "Not a JSON member")
        body = buffer.getvalue()
        self.assertEqual([r["id"] for r in StjClient.iter_records(body, True)], [1, 2])
        for options in ({"max_member_bytes": 5}, {"max_expanded_bytes": 10}):
            with self.assertRaises(ValueError):
                list(StjClient.iter_records(body, True, **options))
        self.assertEqual(list(StjClient.iter_records(b'[{"id":3}]')), [{"id": 3}])
        with self.assertRaises(ValueError):
            list(StjClient.iter_records(b'{"id":3}'))

    def test_historical_zip_rejects_missing_or_invalid_json(self):
        for name, data in (("readme.txt", "Instructions"), ("broken.json", '{"id":1}')):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                bundle.writestr(name, data)
            with self.assertRaises(ValueError):
                list(StjClient.iter_records(buffer.getvalue(), True))

    def test_cjf_ajax_pagination_keeps_state_and_original_bytes(self):
        home = b'''<form id="formulario" action="/trf1/index.xhtml"><input name="javax.faces.ViewState" value="initial">
        <input name="formulario:base" value="JEF1"></form>'''
        first = b'''<form id="formulario"><input name="javax.faces.ViewState" value="next"></form>
        <script id="formulario:tabelaDocumentos_s">rows:30,rowCount:60</script>'''
        partial = b'''<partial-response><changes><update id="formulario:tabelaDocumentos"><![CDATA[<div id="item_resultado-2"></div>]]></update>
        <update id="javax.faces.ViewState"><![CDATA[last]]></update></changes></partial-response>'''
        http = Mock()
        http.fetch.side_effect = [home, first, partial]
        pages = list(CjfClient(http).pages("LOAS", "JEF1", 2))
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[1][0], partial)
        self.assertIn(b"item_resultado-2", pages[1][1])
        payload = dict(http.fetch.call_args.args[1])
        self.assertEqual(payload["formulario:tabelaDocumentos_first"], "30")
        self.assertEqual(payload["javax.faces.ViewState"], "next")
        self.assertEqual(http.fetch.call_args.kwargs["headers"]["Faces-Request"], "partial/ajax")

    def test_archive_observed_negative_contract_and_no_fabricated_document(self):
        http = Mock()
        http.fetch.return_value = b'{"existeProcesso":false,"retorno":"Nao localizado","params":"35"}'
        body, result = ArchiveClient(http).lookup("1053078-37.2022.4.01.3400")
        self.assertFalse(result["existeProcesso"])
        self.assertEqual(http.fetch.call_args.args[1], [("ProcInclui", "10530783720224013400")])
        http.fetch.return_value = b'{"erro":"Contrato mudou"}'
        with self.assertRaises(ValueError):
            ArchiveClient(http).lookup("1053078-37.2022.4.01.3400")

    def test_postgres_document_ddl_preserves_types_and_foreign_keys(self):
        ddl = str(CreateTable(DocumentoPublico.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn("JSONB", ddl)
        self.assertIn("UUID", ddl)
        self.assertIn("FOREIGN KEY(coleta_id) REFERENCES coletas", ddl)
        self.assertIn("uq_documento_publico_versao", ddl)

    def test_versions_exact_links_api_and_no_new_processes(self):
        engine = make_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine)
        run = str(uuid.uuid4())
        number = "1053078-37.2022.4.01.3400"
        document = stj_document({"id": "1", "numeroProcesso": number, "ementa": "BPC LOAS"}, "https://example.test/a.json")
        try:
            with sessions.begin() as session:
                session.add(Coleta(id=uuid.UUID(run), fonte="stj"))
                session.add(Processo(numero_processo=number))
            with sessions.begin() as session:
                self.assertTrue(store_document(session, document, run, Path("raw.json.gz")))
                self.assertFalse(store_document(session, document, run, Path("other.json.gz")))
                modified = stj_document(dict(document["payload"], ementa="BPC LOAS versão nova"), document["recurso_url"])
                self.assertTrue(store_document(session, modified, run, Path("new.json.gz")))
            with sessions() as session:
                self.assertEqual(len(list(session.scalars(select(DocumentoPublico)))), 2)
                self.assertEqual(len(list(session.scalars(select(DocumentoProcesso)))), 2)
                self.assertEqual(len(list(session.scalars(select(Processo)))), 1)
            def dependency():
                with sessions() as session:
                    yield session
            api.app.dependency_overrides[api.get_session] = dependency
            client = TestClient(api.app)
            response = client.get("/admin/api/documentos?fonte=stj&texto=LOAS")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 2)
            detail = client.get(f"/admin/api/processos/{number}")
            self.assertEqual(len(detail.json()["documentos_publicos"]), 2)
        finally:
            api.app.dependency_overrides.clear()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
