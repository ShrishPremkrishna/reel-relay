"""Fenced worker leases, delayed retries, settings, encrypted credentials and OAuth states."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    op.add_column("candidates", sa.Column("claim_token", sa.String(64)))
    op.add_column("candidates", sa.Column("available_at", sa.Float, nullable=False, server_default="0"))
    op.add_column("candidates", sa.Column("publishing_account_id", sa.String(128)))
    op.create_index("available_work", "candidates", ["status", "available_at", "created_at"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "vault",
        sa.Column("name", sa.String(128), primary_key=True),
        sa.Column("ciphertext", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
    )
    op.create_table(
        "oauth_flows",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
    )


def downgrade():
    raise RuntimeError("Downgrades are intentionally disabled; restore a verified database backup instead")
