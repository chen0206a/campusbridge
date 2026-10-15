"""Initial business schema, frozen independently of the live ORM."""
from alembic import op
from campusbridge.schema_v1 import metadata

revision = "d1_0001"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    metadata().create_all(op.get_bind(), checkfirst=False)


def downgrade():
    metadata().drop_all(op.get_bind(), checkfirst=False)
