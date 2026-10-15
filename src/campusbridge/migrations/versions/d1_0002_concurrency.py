"""Database fences and lookup indexes for serialized business mutations."""
from alembic import op
import sqlalchemy as sa

revision = "d1_0002"
down_revision = "d1_0001"
branch_labels = depends_on = None


def upgrade():
    # A unique index is equally enforced by SQLite and InnoDB, without table copying.
    op.create_index("uq_messages_conversation_order", "messages", ["conversation_id", "order_index"], unique=True)
    op.create_index("ix_support_cases_campus_state_created", "support_cases", ["campus_id", "state", "created_at"])
    op.create_index("ix_agent_runs_state_created", "agent_runs", ["state", "created_at"])


def downgrade():
    op.drop_index("ix_agent_runs_state_created", table_name="agent_runs")
    op.drop_index("ix_support_cases_campus_state_created", table_name="support_cases")
    op.drop_index("uq_messages_conversation_order", table_name="messages")
