import gzip
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from legacy_doc import LegacyDocError

from sqlalchemy.orm import Session

from bpc_ingestion.archive_text import converted_document, pending_archive_text
from bpc_ingestion.database import make_engine
from bpc_ingestion.models import Base, DocumentoPublico
from bpc_ingestion.public_sources import ArchiveClient


def synthetic_word(text):
    """Small synthetic OLE container with one UTF-16 Word piece, no personal data."""
    end, free = 0xFFFFFFFE, 0xFFFFFFFF
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into('<HHH', header, 0x1C, 0xFFFE, 9, 6)
    for offset, value in ((0x2C, 1), (0x30, 0), (0x38, 4096), (0x3C, end), (0x44, end)):
        struct.pack_into('<I', header, offset, value)
    struct.pack_into('<109I', header, 0x4C, 1, *([free] * 108))
    directory = bytearray(512)
    for index, (name, kind, sector, size) in enumerate((('Root Entry', 5, end, 0),
             ('WordDocument', 2, 2, 4096), ('0Table', 2, 10, 4096))):
        start = index * 128
        encoded = (name + '\x00').encode('utf-16le')
        directory[start:start+len(encoded)] = encoded
        struct.pack_into('<H', directory, start+64, len(encoded))
        directory[start+66] = kind
        struct.pack_into('<IQ', directory, start+116, sector, size)
    fat = [free] * 128
    fat[0], fat[1] = end, 0xFFFFFFFD
    for first in (2, 10):
        for sector in range(first, first+7):
            fat[sector] = sector+1
        fat[first+7] = end
    word, table = bytearray(4096), bytearray(4096)
    raw = text.encode('utf-16le')
    struct.pack_into('<II', word, 0x18, 512, 512+len(raw))
    word[512:512+len(raw)] = raw
    piece = struct.pack('<IIHIH', 0, len(raw)//2, 0, 512, 0)
    table[:5+len(piece)] = b'\x02' + struct.pack('<I', len(piece)) + piece
    return bytes(header + directory + struct.pack('<128I', *fat) + word + table)


class ArchiveTextTest(unittest.TestCase):
    def item(self, path, body):
        entry = {'url': 'https://arquivo.trf1.jus.br/AGText/test.doc', 'tipo': 'Ementa',
                 'publicacao': None, 'numero_processo': '0003970-58.2006.4.01.4001'}
        return DocumentoPublico(**ArchiveClient.document(entry, body), arquivo_bruto=str(path))

    def test_real_parser_synthetic_text_and_preliminary_provenance(self):
        with tempfile.TemporaryDirectory() as work:
            body = synthetic_word('Benefício assistencial.\rTexto de teste sem dados pessoais.')
            path = Path(work) / 'test.doc.gz'
            with gzip.open(path, 'wb') as output:
                output.write(body)
            item = self.item(path, body)
            converted = converted_document(item, Path(work))
            self.assertIn('Benefício assistencial.', converted['texto'])
            self.assertEqual(item.payload['extracao_texto'], 'pendente')
            self.assertIsNone(item.texto)
            self.assertEqual(converted['payload']['extracao_texto'], 'doc_legacy_preliminar')
            self.assertEqual(converted['payload']['sha256_texto'],
                             hashlib.sha256(converted['texto'].encode()).hexdigest())
            self.assertEqual(converted['payload']['versao_parser_texto'], '0.2.1')

    def test_corrupt_hash_outside_bronze_and_invalid_doc_are_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            body = synthetic_word('Texto de teste')
            path = Path(work) / 'test.doc.gz'
            with gzip.open(path, 'wb') as output:
                output.write(body)
            item = self.item(path, body)
            with self.assertRaises(ValueError):
                converted_document(item, Path(work) / 'other')
            item.payload['sha256_arquivo'] = '0' * 64
            with self.assertRaises(ValueError):
                converted_document(item, Path(work))
            damaged = body[:1024]
            with gzip.open(path, 'wb') as output:
                output.write(damaged)
            item.payload['sha256_arquivo'] = hashlib.sha256(damaged).hexdigest()
            with self.assertRaises(LegacyDocError):
                converted_document(item, Path(work))

    def test_unreadable_output_rejected_and_private_metadata_not_copied(self):
        with tempfile.TemporaryDirectory() as work:
            body = synthetic_word('Texto')
            path = Path(work) / 'test.doc.gz'
            with gzip.open(path, 'wb') as output:
                output.write(body)
            item = self.item(path, body)
            with patch('bpc_ingestion.archive_text.extract_text') as parser:
                for text in (' ', 'Texto \ufffd'):
                    parser.return_value = SimpleNamespace(text=text)
                    with self.assertRaises(ValueError):
                        converted_document(item, Path(work))
                parser.return_value = SimpleNamespace(text='Texto legível', parser='legacy-doc', version='0.2.1',
                    warnings=('Aviso de teste',), metadata={'author': 'Autor privado'})
                converted = converted_document(item, Path(work))
                self.assertNotIn('author', converted['payload'])
                self.assertEqual(converted['payload']['avisos_extracao'], ['Aviso de teste'])

    def test_selection_deduplicates_file_and_skips_converted_pipeline(self):
        engine = make_engine('sqlite://')
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as session:
                # Selection depends only on source payload, not remote URLs or private text.
                from datetime import datetime, timezone
                import uuid
                from bpc_ingestion.models import Coleta
                run = uuid.uuid4()
                session.add(Coleta(id=run, fonte='test'))
                for index in range(2):
                    item = self.item(Path('data/raw/test.doc.gz'), synthetic_word('Teste'))
                    item.hash_conteudo, item.coleta_id = str(index), run
                    item.coletado_em = datetime.now(timezone.utc)
                    session.add(item)
                session.commit()
                self.assertEqual(len(pending_archive_text(session, 10)), 1)
                converted = session.get(DocumentoPublico, 2)
                converted.payload = dict(converted.payload, versao_pipeline_texto='trf1_doc_v1',
                                         extracao_texto='doc_legacy_preliminar')
                session.commit()
                self.assertEqual(pending_archive_text(session, 10), [])
        finally:
            engine.dispose()
