"""Preserve audit floats exactly instead of MySQL native JSON's numeric rewrite."""
import json
from sqlalchemy import JSON
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.types import TypeDecorator


class ExactJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(LONGTEXT(charset="utf8mb4", collation="utf8mb4_0900_bin"))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is not None and dialect.name == "mysql":
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return value

    def process_result_value(self, value, dialect):
        return json.loads(value) if value is not None and dialect.name == "mysql" else value
