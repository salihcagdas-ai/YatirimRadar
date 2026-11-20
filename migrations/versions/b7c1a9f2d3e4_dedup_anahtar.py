"""Index event keys independently of the source workbook URL.

The ministry can republish an EIA workbook at a new URL. Reuse the record ID
in the URL fragment so a subsequent import can recognize the existing event.

Revision ID: b7c1a9f2d3e4
Revises: e4ea2d6d4637
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c1a9f2d3e4'
down_revision: Union[str, Sequence[str], None] = 'e4ea2d6d4637'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'katalizor_olay', sa.Column('dedup_anahtar', sa.String(length=512), nullable=True)
    )
    op.create_index(
        op.f('ix_katalizor_olay_dedup_anahtar'), 'katalizor_olay', ['dedup_anahtar'],
        unique=False,
    )
    # Use the EIA fragment when present and the original URL for other records.
    op.execute(
        """
        UPDATE katalizor_olay
        SET dedup_anahtar = CASE
            WHEN kaynak_url LIKE '%#%'
                THEN left('ced:' || split_part(kaynak_url, '#', 2), 512)
            ELSE left(kaynak_url, 512)
        END
        WHERE kaynak_url IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_katalizor_olay_dedup_anahtar'), table_name='katalizor_olay')
    op.drop_column('katalizor_olay', 'dedup_anahtar')
