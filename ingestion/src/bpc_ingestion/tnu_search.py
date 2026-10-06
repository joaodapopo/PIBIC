"""Formulário e paginação públicos TNU, observados no portal de jurisprudência."""
from __future__ import annotations

import base64
import math
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .public_sources import PublicHttp
from .published_documents import official_url


HOST = "eproctnu-jur.cjf.jus.br"
BASE = f"https://{HOST}/eproc/externo_controlador.php"
HOME = BASE + "?" + urlencode({"acao": "jurisprudencia@jurisprudencia/pesquisar"})


def form_values(form) -> list[tuple[str, str]]:
    """Serialize successful controls as the public form does; never send passwords."""
    result = []
    for tag in form.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        kind = tag.get("type", "text").lower()
        if kind == "password":
            raise ValueError("Formulário TNU exige senha; coleta pública interrompida")
        if not name or tag.has_attr("disabled") or kind in ("submit", "button", "file", "reset"):
            continue
        if kind in ("checkbox", "radio") and not tag.has_attr("checked"):
            continue
        if tag.name == "select":
            options = tag.find_all("option")
            chosen = [o for o in options if o.has_attr("selected") and not o.has_attr("disabled")]
            if not chosen and not tag.has_attr("multiple") and options:
                chosen = [options[0]]
            result.extend((name, o.get("value", o.get_text())) for o in chosen)
        elif tag.name == "textarea":
            result.append((name, tag.get_text()))
        else:
            result.append((name, tag.get("value", "on" if kind in ("checkbox", "radio") else "")))
    return result


def route(value: str, action: str) -> str:
    target = urljoin(HOME, value)
    url = urlparse(target)
    if (url.scheme != "https" or url.hostname != HOST or url.port not in (None, 443)
            or url.username or url.password or url.fragment or url.path != "/eproc/externo_controlador.php"
            or parse_qs(url.query, keep_blank_values=True) != {"acao": ["jurisprudencia@jurisprudencia/" + action]}):
        raise ValueError("Formulário/paginação TNU fora da rota pública esperada")
    return target


def result_link(value: str) -> str:
    target = urljoin(HOME, value)
    url = urlparse(target)
    query = parse_qs(url.query, keep_blank_values=True)
    highlight = query.pop("termosPesquisados", None)
    if highlight is not None:
        if len(highlight) != 1 or len(highlight[0]) > 2048:
            raise ValueError("Realce de pesquisa TNU inválido")
        try:
            base64.b64decode(highlight[0], validate=True)
        except ValueError as exc:
            raise ValueError("Realce de pesquisa TNU não é base64 válido") from exc
    # Só remove o parâmetro de realce publicado; não fabrica ID de documento.
    canonical_input = url._replace(query=urlencode(query, doseq=True)).geturl()
    source, _, canonical = official_url(canonical_input)
    if source != "tnu":
        raise ValueError("Link de resultado não pertence à TNU")
    return canonical


def page_state(body: bytes, expected_page: int) -> tuple[int, int, list[str]]:
    soup = BeautifulSoup(body, "html.parser")
    # No vazio real, o portal omite value nos totais, mas exibe título explícito.
    headers = [h for h in soup.select("#divResultados h2")
               if re.fullmatch(r"\d+ documentos encontrados", h.get_text(" ", strip=True))]
    total_tags = soup.find_all("input", id="hdnTotalResultado")
    pages_tags = soup.find_all("input", id="hdnTotalPaginas")
    current_tags = soup.find_all("input", id="hdnPaginaAtual")
    if (expected_page == 1 and len(headers) == 1 and headers[0].get_text(" ", strip=True) == "0 documentos encontrados"
            and len(total_tags) == len(pages_tags) == len(current_tags) == 1
            and not total_tags[0].get("value") and not pages_tags[0].get("value")
            and current_tags[0].get("value") == "1" and not soup.select(".resultadoItem")):
        return 0, 0, []
    values = {}
    for key in ("hdnTotalPaginas", "hdnPaginaAtual", "hdnTotalResultado"):
        tags = soup.find_all("input", id=key)
        if len(tags) != 1 or not re.fullmatch(r"\d{1,9}", tags[0].get("value", "")):
            raise ValueError("Metadados de paginação TNU ausentes/ambíguos")
        values[key] = int(tags[0]["value"])
    total, pages = values["hdnTotalResultado"], values["hdnTotalPaginas"]
    if values["hdnPaginaAtual"] != expected_page or pages not in ({0, 1} if total == 0 else {math.ceil(total / 10)}):
        raise ValueError("Página/tamanho de resultado TNU diverge da configuração de 10 itens")
    cards = soup.select(".resultadoItem")
    expected_count = min(10, max(0, total - (expected_page - 1) * 10))
    if len(cards) != expected_count:
        raise ValueError("Página TNU incompleta ou layout de resultados alterado")
    links = []
    for card in cards:
        anchors = card.select("a.inteiroTeor[data-link]")
        if len(anchors) != 1:
            raise ValueError("Resultado TNU sem link único de inteiro teor")
        url = result_link(anchors[0]["data-link"])
        identifier = official_url(url)[1]
        if card.get("id") != "resultado" + identifier or url in links:
            raise ValueError("Identidade duplicada/divergente do resultado TNU")
        links.append(url)
    return pages, total, links


class TnuSearchClient:
    def __init__(self, http: PublicHttp | None = None, on_response=None):
        self.http = http or PublicHttp(allowed_hosts={HOST}, max_bytes=16 * 1024 * 1024)
        self.on_response = on_response
        self.context = {}

    def fetch(self, url, data=None):
        body = self.http.fetch(url, data)
        if self.on_response:
            self.on_response(url, body)  # Bronze antes de qualquer interpretação.
        return body

    def pages(self, query: str, limit: int = 5):
        if not query.strip() or len(query) > 200 or not 1 <= limit <= 100:
            raise ValueError("Use consulta TNU não vazia até 200 caracteres e 1..100 páginas")
        home = BeautifulSoup(self.fetch(HOME), "html.parser")
        form = home.find("form", id="frmJurisprudenciaPesquisa")
        if form is None or form.get("method", "").lower() != "post" or not form.find("input", attrs={"name": "txtPesquisa"}):
            raise ValueError("Formulário público de pesquisa TNU alterado")
        options = home.select("#selOrigem option")
        origins = [o["value"] for o in options if o.get("value") and o.get_text(" ", strip=True) == "TNU"]
        if len(origins) != 1:
            raise ValueError("Origem TNU não identificada no formulário")
        overrides = {"txtPesquisa", "selOrigem[]", "rdoCampo", "chkAgruparResultados"}
        data = [(k, v) for k, v in form_values(form) if k not in overrides]
        data += [("txtPesquisa", query), ("selOrigem[]", origins[0]), ("rdoCampo", "I"), ("chkAgruparResultados", "on")]
        body = self.fetch(route(form.get("action", ""), "listar_resultados"), data)
        total_pages, total, links = page_state(body, 1)
        soup = BeautifulSoup(body, "html.parser")
        form = soup.find("form", id="frmJurisprudenciaResultado")
        pagination = soup.find("input", id="hdnUrlPaginar")
        if form is None or pagination is None:
            raise ValueError("Controles de resultados TNU ausentes")
        target = route(pagination.get("value", ""), "ajax_paginar_resultado")
        fields = form_values(form)
        self.context = {"origem": origins[0], "campo_pesquisa": "I", "agrupar_resultados": True,
                        "itens_pagina": 10, "ordenacao": dict(fields).get("selOrdenacao"),
                        "resultados_informados": total, "paginas_informadas": total_pages}
        yield 1, body, links
        for page in range(2, min(limit, total_pages) + 1):
            data = [(k, str(page) if k == "hdnPaginaAtual" else v) for k, v in fields]
            body = self.fetch(target, data)
            pages, count, links = page_state(body, page)
            if pages != total_pages or count != total:
                raise ValueError("Total TNU mudou durante paginação; não afirmar snapshot completo")
            yield page, body, links
