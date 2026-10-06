"""Corpus complementar de documentos públicos, sem ampliar população DataJud."""
from alembic import op
from bpc_ingestion.models import DocumentoPublico, DocumentoProcesso, IndicadorInssIndeferimento

revision = "20261006_0006"
down_revision = "20260922_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    DocumentoPublico.__table__.create(op.get_bind(), checkfirst=True)
    DocumentoProcesso.__table__.create(op.get_bind(), checkfirst=True)
    IndicadorInssIndeferimento.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    IndicadorInssIndeferimento.__table__.drop(op.get_bind(), checkfirst=True)
    DocumentoProcesso.__table__.drop(op.get_bind(), checkfirst=True)
    DocumentoPublico.__table__.drop(op.get_bind(), checkfirst=True)
