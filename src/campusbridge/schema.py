"""Alembic upgrade/check, plus explicit structural adoption of a legacy SQLite DB."""
from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
import sqlalchemy as sa
from campusbridge.schema_v1 import specification

HEAD = "d1_0003"


def config(connection):
    value = Config()
    value.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    value.attributes["connection"] = connection
    return value


def _validate_structure(connection, *, head):
    inspector = inspect(connection)
    for table in specification():
        name = table["name"]
        if name not in inspector.get_table_names(): raise RuntimeError("SCHEMA_TABLE_MISSING:" + name)
        actual = {c["name"]: c for c in inspector.get_columns(name)}
        if set(actual) != {c["name"] for c in table["columns"]}: raise RuntimeError("SCHEMA_COLUMNS_MISMATCH:" + name)
        for c in table["columns"]:
            found = actual[c["name"]]
            if found["nullable"] != c["nullable"]: raise RuntimeError("SCHEMA_NULLABILITY_MISMATCH:" + name)
            expected_type = c["type"]
            if head and expected_type == "JSON" and connection.dialect.name == "mysql": expected_type = "LONGTEXT"
            actual_type = str(found["type"]).upper()
            if expected_type == "DOUBLE" and connection.dialect.name == "sqlite" and actual_type in {"FLOAT", "REAL"}:
                continue
            if expected_type.startswith("VARCHAR("):
                matches = isinstance(found["type"], sa.String) and found["type"].length == int(expected_type[8:-1])
            elif expected_type == "DOUBLE":
                matches = isinstance(found["type"], sa.Double)
            elif expected_type == "TEXT": matches = isinstance(found["type"], sa.Text)
            elif expected_type == "JSON": matches = isinstance(found["type"], sa.JSON)
            elif expected_type == "LONGTEXT": matches = found["type"].__class__.__name__ == "LONGTEXT"
            elif expected_type == "BOOLEAN":
                matches = isinstance(found["type"], sa.Boolean) or (found["type"].__class__.__name__ == "TINYINT" and getattr(found["type"], "display_width", None) == 1)
            else: matches = actual_type == expected_type
            if not matches:
                raise RuntimeError("SCHEMA_TYPE_MISMATCH:" + name + "." + c["name"])
        expected_pk = [c["name"] for c in table["columns"] if c["primary_key"]]
        if inspector.get_pk_constraint(name)["constrained_columns"] != expected_pk: raise RuntimeError("SCHEMA_PRIMARY_KEY_MISMATCH:" + name)
        unique = {tuple(v["column_names"]) for v in inspector.get_unique_constraints(name)}
        unique |= {tuple(v["column_names"]) for v in inspector.get_indexes(name) if v["unique"]}
        expected = {tuple(v) for v in table["unique"]}
        if head and name == "messages": expected.add(("conversation_id", "order_index"))
        if not expected <= unique: raise RuntimeError("SCHEMA_UNIQUE_FENCE_MISSING:" + name)
        fks = {(tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"])) for f in inspector.get_foreign_keys(name)}
        for c in table["columns"]:
            for fk in c["foreign_keys"]:
                parent, column = fk.split(".")
                if ((c["name"],), parent, (column,)) not in fks: raise RuntimeError("SCHEMA_FOREIGN_KEY_MISSING:" + name)
        indexes = {tuple(v["column_names"]) for v in inspector.get_indexes(name)} | unique
        expected_indexes = {tuple(v["columns"]) for v in table["indexes"]}
        if head and name == "support_cases": expected_indexes.add(("campus_id", "state", "created_at"))
        if head and name == "agent_runs": expected_indexes.add(("state", "created_at"))
        if not expected_indexes <= indexes: raise RuntimeError("SCHEMA_INDEX_MISSING:" + name)
        if connection.dialect.name == "mysql":
            options = inspector.get_table_options(name)
            if options.get("mysql_engine") != "InnoDB" or options.get("mysql_collate") != "utf8mb4_0900_bin":
                raise RuntimeError("MYSQL_TABLE_ENGINE_OR_COLLATION_MISMATCH:" + name)
            if head:
                checks = {v["name"]:v["sqltext"].lower() for v in inspector.get_check_constraints(name)}
                for column in table["columns"]:
                    if column["type"] == "JSON":
                        key="ck_"+name+"_"+column["name"]+"_json"
                        if "json_valid" not in checks.get(key,""): raise RuntimeError("JSON_VALID_CHECK_MISSING:"+name)


def migrate(engine, target="head", *, adopt_legacy=False):
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        business = {t["name"] for t in specification()}
        if "alembic_version" not in tables and tables & business:
            if not adopt_legacy or engine.dialect.name != "sqlite": raise RuntimeError("LEGACY_SQLITE_REQUIRES_EXPLICIT_ADOPTION")
            _validate_structure(connection, head=False)
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first(): raise RuntimeError("LEGACY_FOREIGN_KEY_VIOLATION")
            if connection.execute(text("SELECT conversation_id,order_index FROM messages GROUP BY conversation_id,order_index HAVING COUNT(*)>1")).first():
                raise RuntimeError("LEGACY_MESSAGE_ORDER_DUPLICATES")
            connection.commit()
            command.stamp(config(connection), "d1_0001")
            connection.commit()
        elif connection.in_transaction(): connection.commit()
        command.upgrade(config(connection), target)
        connection.commit()


def verify_schema(engine):
    with engine.connect() as connection:
        if MigrationContext.configure(connection).get_current_heads() != (HEAD,): raise RuntimeError("DATABASE_MIGRATION_REQUIRED")
        if engine.dialect.name == "mysql" and (engine.dialect.is_mariadb or engine.dialect.server_version_info < (8, 0)):
            raise RuntimeError("MYSQL_8_REQUIRED")
        _validate_structure(connection, head=True)
    return {"revision": HEAD, "business_tables": len(specification()), "verified": True}
