import gzip
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select

from bpc_ingestion.cli import collect_tnu_search
from bpc_ingestion.config import Settings
from bpc_ingestion.models import Base, Coleta, DocumentoPublico, Processo
from bpc_ingestion.postgres_store import PostgresStore
from bpc_ingestion.tnu_search import HOME, TnuSearchClient, form_values, page_state, result_link, route


NUMBER = "5006875-14.2022.4.04.7005"
HOME_HTML = b'''<form id="frmJurisprudenciaPesquisa" method="post"
action="externo_controlador.php?acao=jurisprudencia@jurisprudencia/listar_resultados">
<input name="txtPesquisa"><select id="selOrigem" name="selOrigem[]" disabled multiple>
<option value="42">TNU</option></select></form>'''


def result_page(page=1, total=2, with_form=True):
    pages = (total + 9) // 10
    result = f'''<input id="hdnTotalPaginas" name="hdnTotalPaginas" value="{pages}">
    <input id="hdnPaginaAtual" name="hdnPaginaAtual" value="{page}">
    <input id="hdnTotalResultado" name="hdnTotalResultado" value="{total}">
    <input id="hdnUrlPaginar" name="hdnUrlPaginar"
    value="externo_controlador.php?acao=jurisprudencia@jurisprudencia/ajax_paginar_resultado">
    <input name="txtPesquisa" value="LOAS"><input name="rdoCampo" value="I">
    <select name="selTamanhoPagina"><option value="10" selected>10</option></select>'''
    for index in range((page - 1) * 10, min(page * 10, total)):
        identifier = str(100 + index)
        result += f'''<div class="resultadoItem" id="resultado{identifier}">
        <a class="inteiroTeor" data-link="externo_controlador.php?acao=jurisprudencia@jurisprudencia/download_inteiro_teor&amp;id_jurisprudencia={identifier}&amp;termosPesquisados=bG9hcw==">link</a></div>'''
    if with_form:
        result = '<form id="frmJurisprudenciaResultado">' + result + '</form>'
    return result.encode()


def document(identifier):
    return f'''<html><article><header id="{identifier}_1">
    <p class="identificacao_processo">Pedido Nº {NUMBER}</p></header>
    <section><p class="titulo">VOTO</p>Benefício assistencial BPC.</section></article></html>'''.encode()


class TnuSearchTest(unittest.TestCase):
    def test_form_serialization_and_password_guard(self):
        soup = BeautifulSoup('''<form><input name="h" type="hidden" value="v">
        <input name="checked" type="checkbox" checked><input name="unchecked" type="checkbox">
        <input name="disabled" disabled><select name="multiple" multiple>
        <option value="a" selected>A</option><option value="b">B</option></select>
        <select name="single"><option value="c">C</option></select></form>''', "html.parser")
        self.assertEqual(form_values(soup.form), [("h", "v"), ("checked", "on"), ("multiple", "a"), ("single", "c")])
        with self.assertRaises(ValueError):
            form_values(BeautifulSoup('<form><input type="password"></form>', 'html.parser').form)

    def test_routes_and_only_published_highlight_removal(self):
        link = 'externo_controlador.php?acao=jurisprudencia@jurisprudencia/download_inteiro_teor&id_jurisprudencia=12&termosPesquisados=bG9hcw=='
        canonical = result_link(link)
        self.assertNotIn('termosPesquisados', canonical)
        self.assertEqual(parse_qs(urlparse(canonical).query)['id_jurisprudencia'], ['12'])
        for bad in [link + '&token=x', link.replace('bG9hcw==', '!invalid'),
                    link.replace('id_jurisprudencia=12', 'id_jurisprudencia=12&id_jurisprudencia=13'),
                    'https://example.org/' + link]:
            with self.assertRaises(ValueError):
                result_link(bad)
        with self.assertRaises(ValueError):
            route('https://example.org/?acao=jurisprudencia@jurisprudencia/listar_resultados', 'listar_resultados')

    def test_page_identity_counts_and_verified_empty(self):
        pages, total, urls = page_state(result_page(), 1)
        self.assertEqual((pages, total, len(urls)), (1, 2, 2))
        self.assertEqual(page_state(result_page(total=0), 1), (0, 0, []))
        for invalid in [b'<html>error</html>', result_page().replace(b'id="resultado100"', b'id="resultado999"'),
                        result_page().replace(b'value="2"', b'value="3"'), result_page().replace(b'card', b'bad').replace(b'resultadoItem', b'bad')]:
            with self.assertRaises(ValueError):
                page_state(invalid, 1)
        with self.assertRaises(ValueError):
            page_state(result_page(page=2, total=12), 1)

    def test_public_form_origin_pagination_and_bronze_before_parse(self):
        http = Mock()
        responses = [HOME_HTML, result_page(total=12), result_page(page=2, total=12, with_form=False)]
        http.fetch.side_effect = responses
        observed = []
        client = TnuSearchClient(http, on_response=lambda url, body: observed.append(body))
        pages = list(client.pages('LOAS', 2))
        self.assertEqual([len(p[2]) for p in pages], [10, 2])
        self.assertEqual(observed, responses)
        self.assertEqual(client.context['origem'], '42')  # valor vindo da origem, não hardcoded.
        initial_data = http.fetch.call_args_list[1].args[1]
        self.assertIn(('selOrigem[]', '42'), initial_data)
        self.assertIn(('txtPesquisa', 'LOAS'), initial_data)
        self.assertIn(('hdnPaginaAtual', '2'), http.fetch.call_args_list[2].args[1])
        http.fetch.side_effect = [HOME_HTML, b'<html>error</html>']
        observed.clear()
        with self.assertRaises(ValueError):
            list(client.pages('LOAS', 2))
        self.assertEqual(observed, [HOME_HTML, b'<html>error</html>'])

    def test_observed_zero_result_has_omitted_numeric_values(self):
        empty = b'''<div id="divResultados"><h2>0 documentos encontrados</h2>
        <input id="hdnTotalResultado"><input id="hdnTotalPaginas">
        <input id="hdnPaginaAtual" value="1"></div>'''
        self.assertEqual(page_state(empty, 1), (0, 0, []))
        with self.assertRaises(ValueError):
            page_state(empty.replace(b'0 documentos encontrados', b'erro'), 1)
        with self.assertRaises(ValueError):
            page_state(empty, 2)

    def test_changed_total_or_missing_origin_rejected(self):
        http = Mock()
        http.fetch.side_effect = [HOME_HTML, result_page(total=12), result_page(page=2, total=13, with_form=False)]
        with self.assertRaises(ValueError):
            list(TnuSearchClient(http).pages('LOAS', 2))
        http.fetch.side_effect = [HOME_HTML.replace(b'>TNU<', b'>Other<')]
        with self.assertRaises(ValueError):
            list(TnuSearchClient(http).pages('LOAS', 2))

    def test_command_resume_then_failure_preserves_success_and_bronze(self):
        with tempfile.TemporaryDirectory() as work:
            db_url = 'sqlite:///' + str(Path(work) / 'base.sqlite')
            store = PostgresStore(db_url)
            Base.metadata.create_all(store.engine)
            with store.Session.begin() as session:
                session.add(Processo(numero_processo=NUMBER))
            store.close()
            args = SimpleNamespace(query='LOAS', limit=1, paginas=1, timeout=30, retentar=False, raw_dir=Path(work)/'raw')
            settings = Settings(database_url=db_url)
            downloaded = []
            def fetch(url, data=None):
                query = parse_qs(urlparse(url).query)
                action = query['acao'][0].rsplit('/', 1)[-1]
                if action == 'pesquisar':
                    return HOME_HTML
                if action == 'listar_resultados':
                    return result_page()
                identifier = query['id_jurisprudencia'][0]
                downloaded.append(identifier)
                return document(identifier)
            with patch('bpc_ingestion.cli.PublicHttp') as http:
                http.return_value.fetch.side_effect = fetch
                collect_tnu_search(args, settings)
                collect_tnu_search(args, settings)
                collect_tnu_search(args, settings)
            self.assertEqual(downloaded, ['100', '101'])
            args.retentar = True
            args.limit = 2
            def failing_fetch(url, data=None):
                if parse_qs(urlparse(url).query).get('id_jurisprudencia') == ['101']:
                    return b'<html>error</html>'
                return fetch(url, data)
            with patch('bpc_ingestion.cli.PublicHttp') as http:
                http.return_value.fetch.side_effect = failing_fetch
                with self.assertRaises(RuntimeError):
                    collect_tnu_search(args, settings)
            store = PostgresStore(db_url)
            try:
                with store.Session() as session:
                    self.assertEqual(len(list(session.scalars(select(DocumentoPublico)))), 2)
                    self.assertEqual(len(list(session.scalars(select(Processo)))), 1)
                    runs = list(session.scalars(select(Coleta)))
                    parents = [r for r in runs if r.fonte == 'tnu_pesquisa']
                    self.assertEqual(sum(r.status == 'falhou' and r.registros == 1 for r in parents), 1)
                    self.assertTrue(any(r.parametros.get('urls_ja_concluidas') == 2 for r in parents))
                    failure = next(r for r in runs if r.fonte == 'tnu' and r.status == 'falhou')
                    with gzip.open(failure.arquivo_bruto, 'rb') as raw:
                        self.assertEqual(raw.read(), b'<html>error</html>')
            finally:
                store.close()
