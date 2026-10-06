import unittest
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from bpc_ingestion import api
from bpc_ingestion.database import make_engine
from bpc_ingestion.inss_concessions import aggregate_concession_rows, numeric_code, store_concessions
from bpc_ingestion.models import Base, Coleta, IndicadorInssConcessao


HEADER = ('Competência concessão', 'Espécie', 'Espécie', 'Despacho', 'Despacho', 'UF', 'CID')


class ConcessionsTest(unittest.TestCase):
    def test_actual_header_pair_codes_filters_and_no_individual_data(self):
        rows = iter([('Título',), HEADER, (202608, 87, 'BPC', 4, 'Descrição oficial', 'Distrito Federal', 'privado'),
                     (202608, 88, 'BPC', 0, 'Outra descrição', 'DF', 'privado'),
                     (202608, 87, 'BPC', 4, 'Descrição oficial', 'GO', 'privado'),
                     (202608, 1, 'Outro', 4, 'Descrição oficial', 'DF', 'privado'),
                     (202608, 87, 'BPC', 4, 'Descrição oficial', 'Não informado', 'privado')])
        counts, quality = aggregate_concession_rows(rows, 'DF', 202608)
        self.assertEqual(counts, Counter({('DF', 87, 4, 'Descrição oficial'): 1,
                                         ('DF', 88, 0, 'Outra descrição'): 1}))
        self.assertEqual(quality['linhas_lidas'], 5)
        self.assertEqual(quality['bpc_uf_desconhecida'], 1)
        self.assertNotIn('privado', str(counts) + str(quality))

    def test_bad_header_code_and_period_rejected(self):
        with self.assertRaises(ValueError):
            aggregate_concession_rows(iter([('UF', 'Espécie'), ('DF', 87)]), 'DF', 202608)
        for row in ((202607, 87, 'BPC', 4, 'Descrição', 'DF'),
                    (202608, 'BPC', 'BPC', 4, 'Descrição', 'DF'),
                    (202608, 87, 'BPC', None, 'Descrição', 'DF')):
            with self.assertRaises(ValueError):
                aggregate_concession_rows(iter([HEADER, row]), 'DF', 202608)
        self.assertEqual(numeric_code(0), 0)
        with self.assertRaises(ValueError):
            numeric_code(True)

    def test_versions_dedup_disagreement_and_latest_api_snapshot(self):
        engine = make_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        try:
            run = str(uuid.uuid4())
            resource = {'id': 'test', 'url': 'https://example.test/a.xlsx'}
            with Session(engine) as session:
                session.add(Coleta(id=uuid.UUID(run), fonte='inss_concessoes'))
                session.commit()
                counts = Counter({('DF', 87, 4, 'Descrição'): 2})
                self.assertEqual(store_concessions(session, counts, resource, 202608, b'old', Path('a.gz'), run), 1)
                session.commit()
                self.assertEqual(store_concessions(session, counts, resource, 202608, b'old', Path('a.gz'), run), 0)
                with self.assertRaises(ValueError):
                    store_concessions(session, Counter({('DF', 87, 4, 'Descrição'): 3}), resource,
                                      202608, b'old', Path('a.gz'), run)
                store_concessions(session, Counter({('DF', 87, 4, 'Descrição'): 5}), resource,
                                  202608, b'new', Path('b.gz'), run)
                session.commit()
                rows = list(session.scalars(select(IndicadorInssConcessao).order_by(IndicadorInssConcessao.id)))
                rows[0].coletado_em = datetime.now(timezone.utc) - timedelta(days=1)
                session.commit()
            def dependency():
                with Session(engine) as session:
                    yield session
            api.app.dependency_overrides[api.get_session] = dependency
            client = TestClient(api.app)
            response = client.get('/admin/api/concessoes-inss?uf=DF&competencia=202608')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['total'], 1)
            self.assertEqual(response.json()['itens'][0]['quantidade'], 5)
            client.close()
        finally:
            api.app.dependency_overrides.clear()
            engine.dispose()

    def test_postgresql_ddl_has_version_identity_and_collection_fk(self):
        ddl = str(CreateTable(IndicadorInssConcessao.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn('uq_inss_concessao_versao', ddl)
        self.assertIn('FOREIGN KEY(coleta_id) REFERENCES coletas', ddl)
