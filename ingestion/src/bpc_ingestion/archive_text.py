"""Offline, preliminary text extraction; never execute Office or send documents."""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from legacy_doc import ExtractionOptions, extract_text
from sqlalchemy import select

from .models import DocumentoPublico

PIPELINE_VERSION = "trf1_doc_v1"
MAX_FILE_BYTES = 16 * 1024 * 1024


def pending_archive_text(session, limit: int) -> list[DocumentoPublico]:
    rows = list(session.scalars(select(DocumentoPublico)
                               .where(DocumentoPublico.fonte == "trf1_arquivo")
                               .order_by(DocumentoPublico.id)))
    converted = {(row.documento_id, row.payload.get("sha256_arquivo")) for row in rows
                 if row.payload.get("versao_pipeline_texto") == PIPELINE_VERSION}
    result, seen = [], set()
    for row in rows:
        key = (row.documento_id, row.payload.get("sha256_arquivo"))
        if (row.payload.get("extracao_texto") == "pendente" and row.recurso_url.lower().endswith(".doc")
                and key not in converted and key not in seen):
            seen.add(key)
            result.append(row)
            if len(result) >= limit:
                break
    return result


def converted_document(item: DocumentoPublico, raw_root: Path) -> dict:
    root, path = raw_root.resolve(), Path(item.arquivo_bruto).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Arquivo de conversão está fora da Bronze permitida")
    with gzip.open(path, "rb") as source:
        body = source.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise ValueError("DOC excede 16 MiB")
    digest = hashlib.sha256(body).hexdigest()
    if digest != item.payload.get("sha256_arquivo"):
        raise ValueError("SHA-256 do DOC diverge da coleta; não converter")
    result = extract_text(body, options=ExtractionOptions(max_file_bytes=MAX_FILE_BYTES,
                          max_text_bytes=2 * 1024 * 1024, max_chain_sectors=32768))
    if not result.text.strip() or "\ufffd" in result.text:
        raise ValueError("DOC não produziu texto legível sem substituições")
    payload = dict(item.payload, extracao_texto="doc_legacy_preliminar", parser_texto=result.parser,
                   versao_parser_texto=result.version, versao_pipeline_texto=PIPELINE_VERSION,
                   sha256_texto=hashlib.sha256(result.text.encode()).hexdigest(),
                   avisos_extracao=list(result.warnings))
    fields = ("fonte", "documento_id", "tribunal", "tipo_documento", "numero_origem", "numeros_cnj",
              "data_publicacao", "data_decisao", "ementa", "decisao", "url_origem", "recurso_url")
    return {**{field: getattr(item, field) for field in fields}, "texto": result.text, "payload": payload}
