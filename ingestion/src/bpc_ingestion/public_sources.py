"""Coleta conservadora de documentos públicos oficiais, com origem e versões."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup
from sqlalchemy import String, cast, inspect, or_, select

from .models import Coleta, DocumentoProcesso, DocumentoPublico, Processo, RegistroAssunto, RegistroDatajud


class SourceBlocked(RuntimeError):
    pass


def check_blocked(text: str) -> None:
    markers = ("cf-chl", "challenge-platform", "g-recaptcha-response", "h-captcha",
               'class="g-recaptcha"', "verificação anti-robô")
    if any(marker in text.lower() for marker in markers):
        raise SourceBlocked("Fonte exige verificação anti-robô/CAPTCHA; coleta interrompida, sem contorno")


class OfficialRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlparse(newurl)
        if (url.scheme != "https" or url.hostname not in self.allowed_hosts
                or url.port not in (None, 443) or url.username or url.password):
            raise ValueError("Redirecionamento fora da origem oficial permitida")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicHttp:
    def __init__(self, timeout: float = 60, interval: float = 1, max_bytes: int = 64 * 1024 * 1024,
                 allowed_hosts: set[str] | None = None, form_encoding: str = "utf-8"):
        self.timeout, self.interval, self.max_bytes = timeout, interval, max_bytes
        self.form_encoding = form_encoding
        handlers = [HTTPCookieProcessor(CookieJar())]
        if allowed_hosts is not None:
            handlers.append(OfficialRedirectHandler(allowed_hosts))
        self.allowed_hosts = allowed_hosts
        self.opener = build_opener(*handlers)
        self.last = 0.0

    def fetch(self, url: str, data: list[tuple[str, str]] | None = None,
              headers: dict[str, str] | None = None) -> bytes:
        if not url.startswith("https://"):
            raise ValueError("Fonte pública deve usar HTTPS")
        if self.allowed_hosts is not None and urlparse(url).hostname not in self.allowed_hosts:
            raise ValueError("URL fora da origem oficial permitida")
        wait = self.interval - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        request = Request(url, headers={"User-Agent": "bpc-pesquisa/0.6", "Accept": "*/*", **(headers or {})},
                          data=urlencode(data, encoding=self.form_encoding).encode("ascii") if data is not None else None)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if int(response.headers.get("Content-Length") or 0) > self.max_bytes:
                    raise ValueError("Arquivo excede o limite de download")
                body = response.read(self.max_bytes + 1)
        except HTTPError as exc:
            excerpt = exc.read(65536).decode("utf-8", errors="replace")
            check_blocked(excerpt)
            raise RuntimeError(f"Fonte pública retornou HTTP {exc.code}: {url}") from exc
        if len(body) > self.max_bytes:
            raise ValueError("Arquivo excede o limite de download")
        if body.lstrip().startswith(b"<"):
            check_blocked(body.decode("utf-8", errors="replace"))
        return body


def preserve_raw(body: bytes, root: Path, source: str, run: str, suffix: str) -> Path:
    directory = root / source / run
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.{suffix}.gz"
    with path.open("xb") as output, gzip.GzipFile(fileobj=output, mode="wb") as compressed:
        compressed.write(body)
    return path


def cnj_number(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not (re.fullmatch(r"\d{20}", raw) or re.fullmatch(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", raw)):
        return None
    digits = re.sub(r"\D", "", raw)
    return f"{digits[:7]}-{digits[7:9]}.{digits[9:13]}.{digits[13]}.{digits[14:16]}.{digits[16:]}"


def public_date(value: Any) -> date | None:
    text = str(value or "")
    for pattern, fmt in ((r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
                         (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"), (r"\b\d{8}\b", "%Y%m%d")):
        match = re.search(pattern, text)
        if match:
            try:
                return datetime.strptime(match.group(), fmt).date()
            except ValueError:
                continue
    return None


def stj_document(item: dict[str, Any], resource_url: str) -> dict[str, Any]:
    if not item.get("id"):
        raise ValueError("Espelho STJ sem ID estável")
    numbers = {number for key in ("numeroProcesso", "numeroProcessoCNJ", "numeroCnj")
               if (number := cnj_number(item.get(key)))}
    return {"fonte": "stj", "documento_id": str(item["id"]), "tribunal": "STJ",
            "tipo_documento": str(item.get("tipoDeDecisao") or "espelho_acordao"),
            "numero_origem": str(item.get("numeroProcesso") or ""), "numeros_cnj": sorted(numbers),
            "data_publicacao": public_date(item.get("dataPublicacao")),
            "data_decisao": public_date(item.get("dataDecisao")),
            "ementa": item.get("ementa"), "decisao": item.get("decisao"), "texto": None,
            "url_origem": resource_url,
            "recurso_url": resource_url, "payload": item}


def bpc_text(item: dict[str, Any]) -> bool:
    # Busca temática explícita, não classificação de concessão inicial/resultado.
    text = " ".join(str(item.get(key) or "") for key in ("ementa", "decisao", "teseJuridica", "informacoesComplementares"))
    return bool(re.search(r"benef[ií]cio\s+assistencial|presta[çc][ãa]o\s+continuada|\b(?:LOAS|BPC)\b|\b8[.]?742\b", text, re.I))


class StjClient:
    BASE = "https://dadosabertos.web.stj.jus.br/api/3/action"

    def __init__(self, http: PublicHttp | None = None):
        self.http = http or PublicHttp()

    def package(self, name: str) -> dict[str, Any]:
        self.package_raw = self.http.fetch(f"{self.BASE}/package_show?{urlencode({'id': name})}")
        result = json.loads(self.package_raw)
        if not result.get("success") or not isinstance(result.get("result"), dict):
            raise ValueError("Catálogo STJ sem sucesso")
        return result["result"]

    @staticmethod
    def recent_json(package: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        resources = [r for r in package.get("resources", [])
                     if str(r.get("format", "")).upper() == "JSON" and r.get("url")]
        return sorted(resources, key=lambda r: r["url"].rsplit("/", 1)[-1], reverse=True)[:limit]

    @staticmethod
    def historical_zip(package: dict[str, Any]) -> list[dict[str, Any]]:
        return sorted((r for r in package.get("resources", [])
                       if str(r.get("format", "")).upper() == "ZIP" and r.get("url")),
                      key=lambda r: r["url"])

    @staticmethod
    def iter_records(body: bytes, archive: bool = False, *, max_member_bytes: int = 128 * 1024 * 1024,
                     max_expanded_bytes: int = 1024 * 1024 * 1024):
        """Read JSON members in memory, never extract archive paths to disk."""
        def rows(data):
            result = json.loads(data.decode("utf-8-sig"))
            if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
                raise ValueError("Arquivo STJ não é lista de espelhos")
            yield from result

        if not archive:
            yield from rows(body)
            return
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            members = bundle.infolist()
            if len(members) > 10000 or sum(i.file_size for i in members) > max_expanded_bytes:
                raise ValueError("Histórico STJ excede limite expandido de segurança")
            if any(i.flag_bits & 1 for i in members):
                raise ValueError("Histórico STJ criptografado não é suportado")
            selected = [i for i in members if not i.is_dir() and i.filename.lower().endswith(".json")]
            if not selected:
                raise ValueError("Histórico STJ sem membros JSON")
            if any(i.file_size > max_member_bytes for i in selected):
                raise ValueError("Membro JSON STJ excede limite de segurança")
            for member in selected:
                with bundle.open(member) as source:
                    data = source.read(max_member_bytes + 1)
                if len(data) > max_member_bytes:
                    raise ValueError("Membro JSON STJ excede limite de segurança")
                yield from rows(data)

    def records(self, resource: dict[str, Any]) -> tuple[bytes, list[dict[str, Any]]]:
        body = self.http.fetch(resource["url"])
        return body, list(self.iter_records(body))


class CjfClient:
    URL = "https://jurisprudencia.cjf.jus.br/trf1/index.xhtml"

    def __init__(self, http: PublicHttp | None = None):
        self.http = http or PublicHttp()

    def search(self, query: str, base: str = "TRF1") -> bytes:
        if base not in ("TRF1", "JEF1"):
            raise ValueError("Base CJF deve ser TRF1 ou JEF1")
        home = BeautifulSoup(self.http.fetch(self.URL), "html.parser")
        form = home.find("form", id="formulario")
        if form is None:
            raise ValueError("Formulário CJF não encontrado; portal pode ter mudado")
        view = form.find("input", attrs={"name": "javax.faces.ViewState"})
        base_input = form.find("input", attrs={"value": base})
        if view is None or base_input is None:
            raise ValueError("Campos de sessão/base CJF ausentes")
        data = [("formulario", "formulario"), ("javax.faces.ViewState", view["value"]),
                ("formulario:textoLivre", query), ("formulario:selectTiposDocumento", "ACORDAO"),
                ("formulario:selectTiposDocumento", "DECISAOMONO"),
                (base_input["name"], base), ("formulario:actPesquisar", "")]
        return self.http.fetch(urljoin(self.URL, form.get("action") or self.URL), data)

    def pages(self, query: str, base: str = "TRF1", limit: int = 1):
        initial = self.search(query, base)
        yield initial, initial
        soup = BeautifulSoup(initial, "html.parser")
        script = soup.find("script", id="formulario:tabelaDocumentos_s")
        if script is None:
            if re.search(r"(?:0\s+Documento|Nenhum|nenhum|não foram encontrad)", soup.get_text()):
                return
            if limit > 1:
                raise ValueError("Paginação CJF ausente na resposta")
            return
        config = script.get_text()
        size = re.search(r"rows:(\d+)", config)
        count = re.search(r"rowCount:(\d+)", config)
        form = soup.find("form", id="formulario")
        view = form.find("input", attrs={"name": "javax.faces.ViewState"}) if form else None
        if size is None or count is None or view is None:
            raise ValueError("Configuração de paginação CJF mudou")
        rows, total = int(size[1]), int(count[1])
        state = view["value"]
        for page in range(1, min(limit, (total + rows - 1) // rows)):
            data = [("javax.faces.partial.ajax", "true"), ("javax.faces.source", "formulario:tabelaDocumentos"),
                    ("javax.faces.partial.execute", "formulario:tabelaDocumentos"),
                    ("javax.faces.partial.render", "formulario:tabelaDocumentos"),
                    ("formulario:tabelaDocumentos_pagination", "true"),
                    ("formulario:tabelaDocumentos_first", str(page * rows)),
                    ("formulario:tabelaDocumentos_rows", str(rows)),
                    ("formulario:tabelaDocumentos_encodeFeature", "true"),
                    ("formulario", "formulario"), ("javax.faces.ViewState", state)]
            response = self.http.fetch(self.URL, data, headers={"Faces-Request": "partial/ajax"})
            try:
                tree = ET.fromstring(response)
            except ET.ParseError as exc:
                raise ValueError("Paginação CJF não retornou XML JSF") from exc
            content = None
            for update in tree.iter("update"):
                if "ViewState" in update.get("id", ""):
                    state = update.text or state
                elif update.get("id") == "formulario:tabelaDocumentos":
                    content = update.text
            if content is None:
                raise ValueError("Resposta de paginação CJF sem documentos; não avançar silenciosamente")
            yield response, content.encode("utf-8")

    @staticmethod
    def documents(body: bytes, base: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        items = soup.find_all(id=re.compile(r"^item_resultado-"))
        if not items and not re.search(r"(?:0\s+Documento|Nenhum|nenhum|não foram encontrad)", soup.get_text()):
            raise ValueError("Resposta CJF sem documentos nem indicação de resultado vazio; conferir portal")
        result = []
        for item in items:
            fields = {}
            for panel in item.find_all("div", class_="ui-outputpanel"):
                rows = panel.find_all("tr", recursive=False)
                if len(rows) >= 2:
                    label = rows[0].get_text(" ", strip=True).strip(": ")
                    fields[label] = rows[1].get_text(" ", strip=True)
            number = fields.get("Número") or fields.get("Numero") or ""
            numbers = sorted({n for word in number.split() if (n := cnj_number(word))})
            result.append({"fonte": "cjf_trf1", "documento_id": item["id"].removeprefix("item_resultado-"),
                           "tribunal": base, "tipo_documento": fields.get("Tipo"),
                           "numero_origem": number, "numeros_cnj": numbers,
                           "data_publicacao": public_date(fields.get("Data da publicação")),
                           "data_decisao": public_date(fields.get("Data")), "ementa": fields.get("Ementa"),
                           "decisao": fields.get("Decisão"), "texto": item.get_text("\n", strip=True),
                           "url_origem": CjfClient.URL, "recurso_url": CjfClient.URL,
                           "payload": {"id": item["id"], "base": base, "campos": fields}})
        return result


def ensure_document_tables(engine) -> None:
    if "processos" not in inspect(engine).get_table_names():
        raise ValueError("Base da aplicação inexistente; inicialize/restaure o banco primeiro")
    for table in (DocumentoPublico.__table__, DocumentoProcesso.__table__):
        table.create(engine, checkfirst=True)


def cjf_partition(number: str, base: str, pages: int) -> str:
    return f"{number}/{base}/p{pages}/v1"


def pending_cjf_sample(session, base: str, pages: int, limit: int, retry: bool = False):
    """Existing public BPC candidates at Brasília organs; never infer residence."""
    query = (select(Processo).join(RegistroDatajud, RegistroDatajud.processo_id == Processo.id)
             .join(RegistroAssunto, RegistroAssunto.registro_id == RegistroDatajud.id)
             .where(RegistroDatajud.tribunal == "TRF1", RegistroDatajud.grau.in_(("G1", "JE")),
                    cast(RegistroDatajud.payload["orgaoJulgador"]["codigoMunicipioIBGE"].as_string(), String) == "743",
                    or_(RegistroDatajud.nivel_sigilo == 0, RegistroDatajud.nivel_sigilo.is_(None)),
                    RegistroAssunto.assunto_codigo.in_((6114, 11946, 11947)))
             .distinct().order_by(Processo.id))
    completed = set() if retry else set(session.scalars(
        select(Coleta.particao).where(Coleta.fonte == "cjf_amostra",
                                      Coleta.status.in_(("concluida", "sem_resultado")))))
    result = []
    for process in session.scalars(query):
        if cjf_partition(process.numero_processo, base, pages) not in completed:
            result.append(process.numero_processo)
            if len(result) >= limit:
                break
    return result


class ArchiveClient:
    """Endpoint AJAX efetivamente usado pelo formulário público do arquivo TRF1."""
    URL = "https://arquivo.trf1.jus.br/localiza_processo.php"

    def __init__(self, http: PublicHttp | None = None):
        self.http = http or PublicHttp()

    def lookup(self, number: str) -> tuple[bytes, dict[str, Any]]:
        normalized = cnj_number(number)
        if normalized is None:
            raise ValueError("Informe CNJ completo para consultar o arquivo TRF1")
        body = self.http.fetch(self.URL, [("ProcInclui", re.sub(r"\D", "", normalized))])
        result = json.loads(body)
        if not isinstance(result, dict) or type(result.get("existeProcesso")) is not bool:
            raise ValueError("Arquivo TRF1 retornou contrato inesperado")
        return body, result

    def menu(self, number: str, result: dict[str, Any]) -> bytes:
        if result.get("existeProcesso") is not True:
            raise ValueError("Arquivo TRF1 não localizou o processo; não buscar documentos")
        values = {key: str(result.get(key) or "") for key in ("procCNJ", "procTRF")}
        if not all(re.fullmatch(r"\d{10,20}", value) for value in values.values()):
            raise ValueError("Localização TRF1 sem identificadores válidos para a listagem")
        body = self.http.fetch("https://arquivo.trf1.jus.br/PesquisaMenuArquivo.asp",
                               [("pN", values["procCNJ"]), ("pA", values["procTRF"]),
                                ("p1", values["procCNJ"])])
        # Confirm identity before allowing any downloads.
        self.documents(body, number)
        return body

    @staticmethod
    def documents(body: bytes, number: str) -> list[dict[str, Any]]:
        normalized = cnj_number(number)
        soup = BeautifulSoup(body, "html.parser")
        match = re.search(r"Processo\s+Pesquisado:\s*(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})",
                          soup.get_text(" ", strip=True), re.I)
        if normalized is None or match is None or cnj_number(match[1]) != normalized:
            raise ValueError("Listagem TRF1 não confirma o CNJ solicitado")
        result, seen = [], set()
        for row in soup.find_all("tr"):
            for link in row.find_all("a", href=True):
                url = urljoin("https://arquivo.trf1.jus.br/", link["href"])
                parsed = urlparse(url)
                if not re.search(r"\.(?:doc|tif|tiff)$", parsed.path, re.I):
                    continue
                if (parsed.scheme != "https" or parsed.hostname != "arquivo.trf1.jus.br"
                        or parsed.port not in (None, 443) or parsed.username or parsed.password):
                    raise ValueError("Link de documento TRF1 fora da origem HTTPS permitida")
                if url in seen:
                    continue
                seen.add(url)
                result.append({"url": url, "tipo": link.get_text(" ", strip=True) or "Documento",
                               "publicacao": public_date(row.get_text(" ", strip=True)),
                               "numero_processo": normalized})
        if not result:
            raise ValueError("Listagem TRF1 positiva sem links DOC/TIFF reconhecidos")
        return result

    @staticmethod
    def document(entry: dict[str, Any], body: bytes) -> dict[str, Any]:
        suffix = Path(urlparse(entry["url"]).path).suffix.lower()
        text, encoding = None, None
        if suffix == ".doc":
            valid = body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
            if not valid and body.startswith(b"TRIBUNAL REGIONAL FEDERAL") and b"\x00" not in body:
                for encoding in ("utf-8", "cp1252"):
                    try:
                        text = body.decode(encoding)
                        valid = True
                        break
                    except UnicodeDecodeError:
                        continue
        else:
            valid = body.startswith((b"II*\x00", b"MM\x00*"))
        if not valid:
            raise ValueError("Arquivo TRF1 não possui assinatura binária DOC/TIFF esperada")
        number = cnj_number(entry["numero_processo"])
        if number is None:
            raise ValueError("Documento TRF1 sem CNJ confirmado")
        payload = {"numero_processo": number, "tipo": entry["tipo"], "url": entry["url"],
                   "data_publicacao": entry["publicacao"].isoformat() if entry["publicacao"] else None,
                   "sha256_arquivo": hashlib.sha256(body).hexdigest(),
                   "extracao_texto": "texto_simples" if text else "pendente", "encoding_texto": encoding if text else None}
        return {"fonte": "trf1_arquivo", "documento_id": urlparse(entry["url"]).path, "tribunal": "TRF1",
                "tipo_documento": entry["tipo"], "numero_origem": number, "numeros_cnj": [number],
                "data_publicacao": entry["publicacao"], "data_decisao": None,
                "ementa": None, "decisao": None, "texto": text, "url_origem": entry["url"],
                "recurso_url": entry["url"], "payload": payload}


def store_document(session, document: dict[str, Any], run: str, raw: Path) -> bool:
    digest = hashlib.sha256(json.dumps(document["payload"], ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    existing = session.scalar(select(DocumentoPublico).where(
        DocumentoPublico.fonte == document["fonte"], DocumentoPublico.documento_id == document["documento_id"],
        DocumentoPublico.hash_conteudo == digest))
    created = existing is None
    if created:
        existing = DocumentoPublico(**document, hash_conteudo=digest, coleta_id=uuid.UUID(run),
                                   arquivo_bruto=str(raw), coletado_em=datetime.now(timezone.utc))
        session.add(existing)
        session.flush()
    for number in document["numeros_cnj"]:
        process_id = session.scalar(select(Processo.id).where(Processo.numero_processo == number))
        if process_id is not None and session.get(DocumentoProcesso, (existing.id, process_id)) is None:
            session.add(DocumentoProcesso(documento_id=existing.id, processo_id=process_id, criterio="cnj_explicito"))
    return created
