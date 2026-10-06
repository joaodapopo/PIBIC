"""Busca pública no formulário de jurisprudência TRF5 e paginação publicada."""
from __future__ import annotations

import math
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .public_sources import PublicHttp

HOST = "jurisprudencia.trf5.jus.br"
HOME = f"https://{HOST}/jurisprudencia/pesquisa.wsp"
RESULT = f"https://{HOST}/jurisprudencia/resultado_pesquisa.wsp"
DOC = f"https://{HOST}/jurisprudencia/exibir.wsp?tmp.id_documento={{}}"


def _public_route(value: str, path: str = "/jurisprudencia/resultado_pesquisa.wsp") -> str:
    target = urljoin(HOME, value)
    parsed = urlparse(target)
    if (parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment or parsed.path != path
            or parse_qs(parsed.query, keep_blank_values=True)):
        raise ValueError("Formulário TRF5 fora da rota pública observada")
    return f"https://{HOST}{path}"


def _controls(form) -> list[tuple[str, str]]:
    values = []
    for tag in form.find_all(["input", "select", "textarea"]):
        name, kind = tag.get("name"), tag.get("type", "text").lower()
        if kind == "password":
            raise ValueError("Formulário TRF5 exige senha; busca pública interrompida")
        if not name or tag.has_attr("disabled") or kind in {"submit", "button", "file", "reset"}:
            continue
        if kind in {"checkbox", "radio"} and not tag.has_attr("checked"):
            continue
        if tag.name == "select":
            options = tag.find_all("option")
            chosen = [o for o in options if o.has_attr("selected") and not o.has_attr("disabled")]
            if not chosen and not tag.has_attr("multiple") and options:
                chosen = [options[0]]
            values.extend((name, o.get("value", o.get_text())) for o in chosen)
        elif tag.name == "textarea":
            values.append((name, tag.get_text()))
        else:
            values.append((name, tag.get("value", "on" if kind in {"checkbox", "radio"} else "")))
    return values


def _one_form(soup, name: str, action: str, path: str = "/jurisprudencia/resultado_pesquisa.wsp"):
    forms = soup.find_all("form", attrs={"name": name})
    if (len(forms) != 1 or forms[0].get("method", "").lower() != "post"
            or _public_route(forms[0].get("action", ""), path) != action):
        raise ValueError("Formulário TRF5 mudou ou está ambíguo")
    return forms[0]


def _published_links(soup, total: int, offset: int, size: int) -> list[str]:
    table = soup.find("table", class_="grid")
    if table is None:
        if total == 0:
            return []
        raise ValueError("Tabela de resultados TRF5 ausente")
    links, ids = [], set()
    rows = table.find_all("tr")
    expected = min(size, max(0, total - offset))
    for row in rows:
        cells = row.find_all("td", class_="grid", recursive=False)
        if not cells:
            continue
        anchors = cells[0].find_all("a", href=True)
        matches = [re.fullmatch(r"javascript:exibir\((\d{1,100})\)", a["href"].strip()) for a in anchors]
        matches = [m for m in matches if m]
        if len(matches) != 1:
            raise ValueError("Linha TRF5 sem link publicado único de documento")
        identifier = matches[0][1]
        if identifier in ids:
            raise ValueError("ID de documento TRF5 repetido na página")
        ids.add(identifier)
        links.append(DOC.format(identifier))
    if len(links) != expected:
        raise ValueError("Página TRF5 incompleta ou contagem incompatível")
    return links


class Trf5SearchClient:
    def __init__(self, http: PublicHttp | None = None, on_response=None):
        self.http = http or PublicHttp(allowed_hosts={HOST}, max_bytes=16 * 1024 * 1024,
                                       form_encoding="iso-8859-1")
        self.on_response = on_response
        self.context = {}

    def fetch(self, url, data=None):
        body = self.http.fetch(url, data)
        if self.on_response:
            self.on_response(url, body)
        return body

    def pages(self, query: str, limit: int = 5, size: int = 10):
        if not query.strip() or len(query) > 200 or not 1 <= limit <= 100 or size not in {10, 25, 50, 100}:
            raise ValueError("Use consulta até 200 caracteres, 1..100 páginas e tamanho 10/25/50/100")
        home = BeautifulSoup(self.fetch(HOME), "html.parser")
        form = _one_form(home, "formulario", f"https://{HOST}/jurisprudencia/pesquisa.wsp",
                         "/jurisprudencia/pesquisa.wsp")
        fields = dict(_controls(form))
        if "tmp.search.pesquisalivre" not in fields or "tmp.search.qtdade_registros" not in fields:
            raise ValueError("Critérios públicos TRF5 sem campo livre ou tamanho de página")
        response_form = _one_form(home, "formulario_resposta", RESULT)
        allowed = {"tmp.search.query", "tmp.search.query_complemento", "tmp.ds_legislacao_2",
                   "tmp.search.qtdade_registros", "tmp.search.acao"}
        values = dict(_controls(response_form))
        if set(values) != allowed:
            raise ValueError("Campos de consulta TRF5 mudaram")
        values.update({"tmp.search.query": query + " ", "tmp.search.query_complemento": "",
                       "tmp.ds_legislacao_2": "", "tmp.search.qtdade_registros": str(size),
                       "tmp.search.acao": "novapesquisa"})
        body = self.fetch(RESULT, list(values.items()))
        soup = BeautifulSoup(body, "html.parser")
        pager = self._pager(soup, expected_page=1, size=size)
        pages, total, fields, formname, next_offset, links = pager
        self.context = {"itens_pagina": size, "resultados_informados": total,
                        "paginas_informadas": pages, "versao_busca": "trf5_form_v1"}
        yield 1, body, links
        for page in range(2, min(limit, pages) + 1):
            target_form = _one_form(soup, formname, RESULT)
            if next_offset is None:
                raise ValueError("TRF5 não publicou o link da próxima página")
            data = [(key, value) for key, value in fields if key != "grid.pesquisa.next"]
            data.append(("grid.pesquisa.next", str(next_offset)))
            body = self.fetch(RESULT, data)
            soup = BeautifulSoup(body, "html.parser")
            new_pages, new_total, fields, formname, next_offset, links = self._pager(soup, page, size)
            if new_pages != pages or new_total != total:
                raise ValueError("Total TRF5 mudou durante paginação")
            yield page, body, links

    @staticmethod
    def _pager(soup, expected_page: int, size: int):
        forms = [f for f in soup.find_all("form") if f.get("name", "").startswith("wi-")]
        if len(forms) != 1 or forms[0].get("method", "").lower() != "post":
            raise ValueError("Formulário de paginação TRF5 ausente/ambíguo")
        fields = _controls(forms[0])
        values = dict(fields)
        if values.get("tmp.indexname") != "jurisprudencia" or values.get("tmp.search.qtdade_registros") != str(size):
            raise ValueError("Contexto de pesquisa TRF5 divergente")
        totals = {values.get("tmp.search.count"), values.get("tmp.searchresult.count")}
        if len(totals) != 1 or not re.fullmatch(r"\d{1,9}", next(iter(totals), "")):
            raise ValueError("Total TRF5 ausente ou conflitante")
        total = int(next(iter(totals)))
        pages = math.ceil(total / size) if total else 0
        links = _published_links(soup, total, (expected_page - 1) * size, size)
        next_offsets = []
        for anchor in soup.find_all("a", href=True):
            match = re.fullmatch(r"javascript:submitWIGrid\('grid\.pesquisa',(\d{1,9})\)", anchor["href"].strip())
            if match and "Próximo" in anchor.get_text(" ", strip=True):
                next_offsets.append(int(match[1]))
        expected_next = expected_page * size + 1
        if expected_page < pages and next_offsets != [expected_next]:
            raise ValueError("Link de próxima página TRF5 ausente/divergente")
        if expected_page >= pages and next_offsets:
            raise ValueError("TRF5 publicou próxima página após o total informado")
        return pages, total, fields, forms[0].get("name"), next_offsets[0] if next_offsets else None, links
