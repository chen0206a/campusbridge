from alembic import context
from campusbridge.db import Base, KNOWLEDGE_TABLES

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("USE_CAMPUSBRIDGE_MIGRATE_COMMAND_WITH_ENVIRONMENT_URL")
context.configure(connection=connection, target_metadata=Base.metadata,
    include_object=lambda obj, name, kind, reflected, compare_to: kind != "table" or name not in KNOWLEDGE_TABLES,
    compare_type=True, render_as_batch=connection.dialect.name == "sqlite")
with context.begin_transaction():
    context.run_migrations()
