"""Aggregate public INSS concessions, never individual records."""
from alembic import op
from bpc_ingestion.models import IndicadorInssConcessao

revision = "20261006_0007"
down_revision = "20261006_0006"
branch_labels = None
depends_on = None


def upgrade():
    IndicadorInssConcessao.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    IndicadorInssConcessao.__table__.drop(op.get_bind(), checkfirst=True)
