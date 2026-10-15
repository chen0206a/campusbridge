"""Exact audit payload storage; JSON_VALID still enforced by MySQL."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "d1_0003"
down_revision = "d1_0002"
branch_labels = depends_on = None
COLUMNS = (("agent_runs","candidate",True),("agent_runs","approval",True),
           ("run_events","event",False),("risk_events","assessment",False))


def upgrade():
    if op.get_bind().dialect.name != "mysql": return
    for table,column,nullable in COLUMNS:
        op.alter_column(table,column,type_=LONGTEXT(charset="utf8mb4",collation="utf8mb4_0900_bin"),
            existing_type=sa.JSON(),existing_nullable=nullable)
        op.create_check_constraint("ck_"+table+"_"+column+"_json",table,column+" IS NULL OR JSON_VALID("+column+")")


def downgrade():
    if op.get_bind().dialect.name != "mysql": return
    for table,column,nullable in reversed(COLUMNS):
        op.drop_constraint("ck_"+table+"_"+column+"_json",table,type_="check")
        op.alter_column(table,column,type_=sa.JSON(),existing_type=LONGTEXT(),existing_nullable=nullable)
