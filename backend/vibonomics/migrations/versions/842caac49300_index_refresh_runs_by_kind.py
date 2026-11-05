"""index refresh runs by kind

Revision ID: 842caac49300
Revises: ed69fee7cf17
Create Date: 2026-11-05 13:37:10.288474
"""

from collections.abc import Sequence

from alembic import op


revision: str = '842caac49300'
down_revision: str | None = 'ed69fee7cf17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A plain CREATE INDEX: it does not rebuild the table.
    op.create_index('ix_refresh_runs_kind_id', 'refresh_runs', ['kind', 'id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_refresh_runs_kind_id', table_name='refresh_runs')
