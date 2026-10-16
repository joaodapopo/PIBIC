"""Documentos oferecidos pela consulta pública TRF1, sem login ou CAPTCHA.

Executar: python -m bpc_ingestion.pje_public --database-url sqlite:///... --limit 20
Não enumera IDs nem declara acesso aos autos completos. Primeira página apenas.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import String, cast, or_, select

from .models import Coleta, Processo, RegistroAssunto, RegistroDatajud
from .postgres_store import PostgresStore
from .public_sources import PublicHttp, SourceBlocked, cnj_number, preserve_raw, store_document

HOST = "pje1g-consultapublica.trf1.jus.br"
HOME = f"https://{HOST}/consultapublica/ConsultaPublica/listView.seam"
DETAIL_PATH = "/consultapublica/ConsultaPublica/DetalheProcessoConsultaPublica/listView.seam"
DOC_PATH = "/consultapublica/ConsultaPublica/DetalheProcessoConsultaPublica/documentoSemLoginHTML.seam"
VERSION = "pje_publico_v1"
CNJ = r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}"


class DocumentIdentityError(ValueError):
    """Diagnóstico seguro: mensagens fixas, sem texto pessoal ou URL de sessão."""


def soup_html(body: bytes) -> BeautifulSoup:
    soup = BeautifulSoup(body, "html.parser")
    if "\ufffd" in str(soup):
        raise ValueError("HTML com perda de codificação")
    if soup.select('.h-captcha, .g-recaptcha, iframe[src*="captcha"]'):
        raise SourceBlocked("Consulta exige CAPTCHA; sem contorno")
    return soup


def official_url(value: str, path: str, params: set[str]) -> str:
    url = urljoin(HOME, value)
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "https" or parsed.hostname != HOST
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment or parsed.path != path or set(query) != params
            or any(len(v) != 1 or not v[0] for v in query.values())):
        raise ValueError("Link fora da rota pública permitida")
    if not re.fullmatch(r"[a-fA-F0-9]+", query["ca"][0]):
        raise ValueError("Identificador público ca inválido")
    if "idProcessoDoc" in query and not query["idProcessoDoc"][0].isdigit():
        raise ValueError("ID documental inválido")
    return url


def popup_url(node) -> str | None:
    # Apenas literal de URL publicado; nunca executar JavaScript.
    match = re.search(r"openPopUp\(\s*'[^']*'\s*,\s*'([^']+)'", node.get("onclick", ""))
    return match[1] if match else None


def search_form(body: bytes, number: str) -> tuple[str, list[tuple[str, str]]]:
    soup = soup_html(body)
    scripts = "\n".join(s.get_text() for s in soup.find_all("script"))
    # Falhar fechado se a proteção publicada mudar. Não remover proteção.
    if not re.search(r"function\s+executarReCaptcha\s*\(\s*\)\s*\{\s*if\s*\(\s*false\s*\)\s*\{\s*hcaptcha\.execute\(\)\s*;\s*return\s+false\s*;\s*\}\s*executarPesquisa\(\)\s*;\s*\}", scripts):
        raise SourceBlocked("Não confirmado CAPTCHA desativado na consulta pública")
    start = re.search(r"executarPesquisa\s*=\s*function\s*\([^)]*\)\s*\{", scripts)
    if not start:
        raise ValueError("Ação pública executarPesquisa ausente")
    action = re.search(r"'parameters'\s*:\s*\{\s*'([^']+)'\s*:\s*'([^']+)'", scripts[start.end():].split("};", 1)[0])
    form = soup.find("form", id="fPP")
    if not action or not form:
        raise ValueError("Formulário público inválido")
    target = urljoin(HOME, form.get("action", ""))
    parsed = urlparse(target)
    if (parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(re.escape(urlparse(HOME).path) + r"(?:;jsessionid=[A-Za-z0-9._-]+)?", parsed.path)):
        raise ValueError("Destino do formulário fora da consulta pública")
    data = {}
    for item in form.find_all("input", attrs={"name": True}):
        kind = item.get("type", "").lower()
        if kind in ("button", "submit") or (kind in ("radio", "checkbox") and not item.has_attr("checked")):
            continue
        data[item["name"]] = item.get("value", "")
    fields = [key for key in data if key.endswith("numProcesso-inputNumeroProcesso")]
    if len(fields) != 1:
        raise ValueError("Campo CNJ público ausente/ambíguo")
    data[fields[0]] = number
    data.update({"AJAXREQUEST": "_viewRoot", action[1]: action[2]})
    return target, list(data.items())


def result_detail(body: bytes, number: str) -> str | None:
    soup = soup_html(body)
    table = soup.find(id="fPP:processosTable")
    urls = set()
    if table:
        for row in table.select("tbody tr"):
            numbers = set(re.findall(CNJ, row.get_text(" ", strip=True)))
            if numbers != {number}:
                continue
            for link in row.find_all("a"):
                value = popup_url(link)
                if value:
                    urls.add(official_url(value, DETAIL_PATH, {"ca"}))
    if len(urls) == 1:
        return urls.pop()
    if not urls and re.search(r"\b0\s+resultados?\s+encontrados?", soup.get_text(" ", strip=True), re.I):
        return None
    raise ValueError("Pesquisa sem resultado CNJ único nem vazio explícito")


def detail_documents(body: bytes, number: str) -> list[tuple[str, str, str]]:
    soup = soup_html(body)
    text = soup.get_text(" ", strip=True)
    match = re.search(r"N[úu]mero\s+(?:do\s+)?Processo\s*:?\s*(" + CNJ + ")", text, re.I)
    if not match or match[1] != number:
        raise ValueError("CNJ rotulado do detalhe não confere")
    grid = soup.find(id=lambda value: value and value.endswith(":processoDocumentoGridTab"))
    if grid is None:
        raise ValueError("Grade de documentos públicos ausente")
    docs = {}
    for link in grid.select('tbody a[title="Visualizar"]'):
        value = popup_url(link)
        if not value:
            raise ValueError("Link documental publicado inválido")
        url = official_url(value, DOC_PATH, {"ca", "idProcessoDoc"})
        row = link.find_parent("tr")
        label = row.get_text(" ", strip=True)
        # Descritor após data/hora; não selecionar petição que cita sentença.
        descriptor = re.search(r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}\s*-\s*([^()]+)", label)
        if not descriptor:
            continue
        kind = descriptor[1].strip()
        normalized = unicodedata.normalize("NFKD", kind).encode("ascii", "ignore").decode().lower()
        if not re.match(r"^(sentenca|decisao|despacho|acordao)\b", normalized):
            continue
        doc_id = parse_qs(urlparse(url).query)["idProcessoDoc"][0]
        docs[doc_id] = (doc_id, kind, url)
    return list(docs.values())


def parse_document(body: bytes, number: str, doc_id: str, kind: str, url: str) -> dict:
    checked = official_url(url, DOC_PATH, {"ca", "idProcessoDoc"})
    if parse_qs(urlparse(checked).query)["idProcessoDoc"][0] != doc_id:
        raise ValueError("ID do link documental não confere")
    soup = soup_html(body)
    root = soup.select_one(".folha")
    if root is None:
        raise ValueError("Documento sem conteúdo público .folha")
    for node in root.select("script, style, noscript"):
        node.decompose()
    text = root.get_text("\n", strip=True)
    lines = text.splitlines()
    header_lines = []
    for line in lines:
        if re.match(r"^(?:AUTOR|RÉU|REU|RELATOR|RECORRENTE|RECORRIDO|IMPETRANTE|EXEQUENTE|REQUERENTE)\s*:", line, re.I):
            break
        header_lines.append(line)
    header_text = "\n".join(header_lines)[:3000]
    header = re.search(r"^(?:PROCESSO|N[úu]mero\s+Processo)\s*:?\s*(?:N[º°o]?\s*\.?\s*:?\s*)?(" + CNJ + r")(?!\d)", header_text, re.I | re.M)
    # Assinaturas estruturadas vêm antes da busca por rótulo: um precedente
    # no corpo nunca pode substituir o número real isolado do cabeçalho.
    header_number = None
    appeal_header = re.search(
        r"(?:^|\n)Processo Judicial Eletrônico\n"
        r"(?:Turmas Recursais dos Juizados Especiais Federais da SJDF\n"
        r"3ª Relatoria da 1ª Turma Recursal da SJDF\n)?"
        r"RECURSO INOMINADO CÍVEL \(460\)\s+N[º°o]?\s*\.?\s*:?\s*(" + CNJ + r")(?:\n|$)",
        header_text, re.I)
    if header_number is None and appeal_header:
        header_number = appeal_header[1]
    if header_number is None:
        # Layout real de cabeçalho Formosa, não busca genérica de CNJs no corpo.
        if (len(lines) >= 4 and (lines[0].startswith("Juizado Especial ")
                or lines[0] == "Vara Federal Cível e Criminal da SSJ de Formosa-GO")
                and lines[1].startswith("Subseção Judiciária ")
                and re.fullmatch(CNJ, lines[2])
                and lines[3] in ("PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)",
                                 "CUMPRIMENTO DE SENTENÇA CONTRA A FAZENDA PÚBLICA (12078)",
                                 "MANDADO DE SEGURANÇA CÍVEL (120)")
                and root.find(string=lambda s: s and s.strip() == lines[2]) is not None):
            header_number = lines[2]
        elif (len(lines) >= 4 and lines[0].startswith("Seção Judiciária ")
                and re.fullmatch(r"\d+ª Vara Federal de Juizado Especial Cível da SJ[A-Z]{2}", lines[1])
                and re.fullmatch(CNJ, lines[2]) and lines[3].startswith("AUTOR:")
                and root.find(string=lambda s: s and s.strip() == lines[2]) is not None):
            header_number = lines[2]
        elif (len(lines) >= 6 and lines[:5] == ["PODER JUDICIÁRIO", "JUSTIÇA FEDERAL",
                "Seção Judiciária do Estado de Goiás",
                "JUIZADO ESPECIAL FEDERAL - SUBSEÇÃO JUDICIÁRIA DE LUZIÂNIA", 'SENTENÇA TIPO "C"']
                and re.fullmatch(CNJ, lines[5])
                and root.find(string=lambda s: s and s.strip() == lines[5]) is not None):
            header_number = lines[5]
        elif (len(lines) >= 5 and lines[:4] == ["PODER JUDICIÁRIO", "JUSTIÇA FEDERAL",
                "Subseção Judiciária de Formosa-GO",
                "Juizado Especial Cível e Criminal Adjunto à Vara Federal da SSJ de Formosa-GO"]
                and re.fullmatch(CNJ, lines[4])
                and root.find(string=lambda s: s and s.strip() == lines[4]) is not None):
            header_number = lines[4]
    if header_number is None and header:
        header_number = header[1]
    if header_number is None:
        # Formato composto observado: voto seguido do bloco identificador da
        # Turma Recursal. Exige campo estruturado único, não CNJ avulso no voto.
        block_pattern = (r"(?:^|\n)JUSTIÇA FEDERAL\nSEÇÃO JUDICIÁRIA DO ESTADO DE GOIÁS\n"
                         r"PRIMEIRA TURMA RECURSAL DOS JUIZADOS ESPECIAIS FEDERAIS\n"
                         r"GABINETE DA 3ª RELATORIA\nRECURSO JEF N[º°]\s*(" + CNJ + r")(?:\n|$)")
        block_numbers = re.findall(block_pattern, text)
        fields = [node for node in root.find_all("strong")
                  if node.find_parent("blockquote") is None
                  and re.fullmatch(r"RECURSO JEF N[º°]\s*" + CNJ, node.get_text(" ", strip=True))]
        if len(block_numbers) == 1 and len(fields) == 1:
            field_match = re.fullmatch(r"RECURSO JEF N[º°]\s*(" + CNJ + ")", fields[0].get_text(" ", strip=True))
            if field_match[1] == block_numbers[0]:
                header_number = block_numbers[0]
    ids = re.findall(r"ID\s+do\s+documento\s*:?\s*(\d+)", text, re.I)
    if header_number is None:
        raise DocumentIdentityError("CNJ não confirmado no cabeçalho publicado")
    if header_number != number:
        raise DocumentIdentityError("CNJ do cabeçalho diverge do processo solicitado")
    if set(ids) != {doc_id}:
        raise DocumentIdentityError("ID da assinatura diverge do documento solicitado ou está ausente")
    for node in root.select("form, #panelGroupPai"):
        node.decompose()
    text = root.get_text("\n", strip=True)
    payload = {"documento_id": doc_id, "numero_processo": number, "tipo_documento": kind,
               "texto": text, "extracao_texto": "pje_html_publico", "versao_parser": VERSION,
               "revisao_privacidade": "pendente"}
    return {"fonte": "pje_trf1", "documento_id": doc_id, "tribunal": "TRF1",
            "tipo_documento": kind, "numero_origem": number, "numeros_cnj": [number],
            "data_publicacao": None, "data_decisao": None, "ementa": None, "decisao": None,
            "texto": text, "url_origem": HOME, "recurso_url": checked, "payload": payload}


def pending_sample(session, limit: int) -> list[str]:
    municipality = cast(RegistroDatajud.payload["orgaoJulgador"]["codigoMunicipioIBGE"].as_string(), String)
    query = (select(Processo.numero_processo, municipality).join(RegistroDatajud)
             .join(RegistroAssunto, RegistroAssunto.registro_id == RegistroDatajud.id)
             .where(RegistroDatajud.tribunal == "TRF1", RegistroDatajud.grau.in_(("G1", "JE")),
                    municipality.in_(("743", "1798", "2788")),
                    or_(RegistroDatajud.nivel_sigilo == 0, RegistroDatajud.nivel_sigilo.is_(None)),
                    RegistroAssunto.assunto_codigo.in_((6114, 11946, 11947)))
             .distinct().order_by(Processo.numero_processo))
    completed = set(session.scalars(select(Coleta.particao).where(
        Coleta.fonte == "pje_trf1_publico", Coleta.status.in_(("concluida", "sem_resultado", "sem_documentos")))))
    groups = {code: [] for code in ("743", "1798", "2788")}
    for number, code in session.execute(query):
        if f"{number}/v1" not in completed:
            groups[code].append(number)
    result = []
    index = 0
    while len(result) < limit and any(index < len(v) for v in groups.values()):
        for group in groups.values():
            if index < len(group) and group[index] not in result:
                result.append(group[index])
                if len(result) == limit:
                    break
        index += 1
    return result


def collect(store, numbers: list[str], raw_root: Path, max_documents: int, timeout: float) -> dict:
    http = PublicHttp(timeout=timeout, interval=1, max_bytes=16 * 1024 * 1024, allowed_hosts={HOST})
    summary = {"consultados": 0, "documentos_novos": 0, "resultados": [], "interrompida": False}
    for number in numbers:
        with store.Session() as session:
            if not session.scalar(select(Processo.id).where(Processo.numero_processo == number)):
                raise ValueError("CNJ não existe na amostra; não criar população")
            completed = session.scalar(select(Coleta.id).where(Coleta.fonte == "pje_trf1_publico",
                Coleta.particao == f"{number}/v1", Coleta.status.in_(("concluida", "sem_resultado", "sem_documentos"))))
            if completed:
                print(f"{number}: já consultado", flush=True)
                continue
        run = store.start_collection("pje_trf1_publico", f"{number}/v1",
            {"cnj": number, "versao_parser": VERSION, "max_documentos": max_documents,
             "primeira_pagina_apenas": True, "timeout": timeout})
        raws = []
        added = 0
        observed = 0
        stage = "formulario"

        def fetch(url, data=None):
            body = http.fetch(url, data=data)
            raw = preserve_raw(body, raw_root, "pje_trf1_publico", run, "html")
            raws.append({"arquivo": str(raw), "etapa": len(raws)})
            return body, raw

        try:
            home, _ = fetch(HOME)
            action, data = search_form(home, number)
            stage = "resultado"
            response, _ = fetch(action, data)
            detail = result_detail(response, number)
            status = "sem_resultado"
            if detail:
                stage = "detalhe"
                body, _ = fetch(detail)
                docs = detail_documents(body, number)
                status = "sem_documentos"
                for doc_id, kind, url in docs[:max_documents]:
                    stage = f"documento:{doc_id}"
                    content, raw = fetch(url)
                    document = parse_document(content, number, doc_id, kind, url)
                    with store.Session.begin() as session:
                        added += int(store_document(session, document, run, raw))
                    observed += 1
                if observed:
                    status = "concluida"
                with store.Session.begin() as session:
                    row = session.get(Coleta, uuid.UUID(run))
                    row.parametros = {**row.parametros, "links_documentais_alvo": len(docs),
                                      "limitado": len(docs) > max_documents}
            store.finish_collection(run, status, observed, str(raw_root / "pje_trf1_publico" / run))
        except BaseException as exc:
            status = "bloqueada" if isinstance(exc, SourceBlocked) else "falhou"
            # Não publicar cookie/ca/ViewState ou nomes em logs de exceção HTTP.
            reason = str(exc) if isinstance(exc, DocumentIdentityError) else "consulta interrompida; verificar Bronze local"
            store.finish_collection(run, status, observed, str(raw_root / "pje_trf1_publico" / run),
                                    f"{type(exc).__name__}: etapa={stage}; {reason}")
            summary["interrompida"] = True
            print(f"{number}: {status} ({type(exc).__name__}); lote encerrado", flush=True)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        summary["consultados"] += 1
        summary["documentos_novos"] += added
        summary["resultados"].append({"cnj": number, "status": status, "novos": added,
                                      "observados": observed, "coleta": run, "bronze": raws})
        print(f"{number}: {status}; {observed} documentos, {added} novos", flush=True)
        if summary["interrompida"]:
            break
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--limit", type=int, default=20)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--numero", action="append")
    selection.add_argument("--manifest", type=Path, help="JSON local com lista selecionados; não cria processos")
    parser.add_argument("--max-documentos", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 200 or not 1 <= args.max_documentos <= 30 or not 1 <= args.timeout <= 120:
        parser.error("Limites: 1–200 processos, 1–30 documentos, timeout 1–120s")
    store = PostgresStore(args.database_url)
    try:
        if args.manifest:
            numbers = json.loads(args.manifest.read_text(encoding="utf-8"))["selecionados"]
            if not isinstance(numbers, list) or not all(isinstance(n, str) for n in numbers):
                parser.error("Manifesto precisa de lista selecionados de CNJs")
        else:
            with store.Session() as session:
                numbers = args.numero or pending_sample(session, args.limit)
        if len(numbers) > args.limit or any(cnj_number(n) != n for n in numbers):
            parser.error("CNJs completos formatados e quantidade dentro de --limit são obrigatórios")
        result = collect(store, list(dict.fromkeys(numbers)), args.raw_dir, args.max_documentos, args.timeout)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return int(result["interrompida"])
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
