"""Initial durable clipping queue (matches the unversioned local prototype)."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("session_id", sa.String(64)),
        sa.Column("start", sa.Float),
        sa.Column("end", sa.Float),
        *[
            sa.Column(name, sa.Text)
            for name in (
                "source_path",
                "project_path",
                "evidence_path",
                "decision",
                "caption",
                "output_path",
                "review",
                "public_url",
                "permalink",
                "error",
            )
        ],
        sa.Column("source_hash", sa.String(64), unique=True),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("score", sa.Float),
        sa.Column("confidence", sa.Float),
        sa.Column("container_id", sa.String(128)),
        sa.Column("media_id", sa.String(128)),
        sa.Column("published_at", sa.Float),
        sa.Column("lease_until", sa.Float),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_test", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attribution", sa.Text, nullable=False, server_default=""),
        sa.Column("metadata", sa.Text, nullable=False, server_default="{}"),
    )
    op.create_index("work_queue", "candidates", ["status", "created_at"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", sa.Float, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("candidate_id", sa.String(64)),
        sa.Column("detail", sa.Text, nullable=False),
    )
    op.create_table(
        "health",
        sa.Column("channel", sa.String(128), primary_key=True),
        sa.Column("updated_at", sa.Float, nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
    )
    op.create_table(
        "locks",
        sa.Column("name", sa.String(128), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("until", sa.Float, nullable=False),
    )


def downgrade():
    raise RuntimeError("Downgrades are intentionally disabled; restore a verified database backup instead")
