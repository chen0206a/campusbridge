"""Immutable d1_0001 schema snapshot. Later revisions must not edit this module/resource."""
import json
from importlib.resources import files
import sqlalchemy as sa


def specification():
    return json.loads(files("campusbridge").joinpath("resources/business-schema-v1.json").read_text(encoding="utf-8"))


def metadata():
    result = sa.MetaData()
    types = {"INTEGER": sa.Integer, "DOUBLE": sa.Double, "BOOLEAN": sa.Boolean, "TEXT": sa.Text, "JSON": sa.JSON}
    for table in specification():
        columns = []
        for c in table["columns"]:
            value = c["type"]
            kind = sa.String(int(value[8:-1])) if value.startswith("VARCHAR(") else types[value]()
            columns.append(sa.Column(c["name"], kind, *(sa.ForeignKey(f) for f in c["foreign_keys"]),
                nullable=c["nullable"], primary_key=c["primary_key"]))
        constraints = [sa.UniqueConstraint(*names, name="uq_" + table["name"] + "_" + "_".join(names)) for names in table["unique"]]
        row = sa.Table(table["name"], result, *columns, *constraints,
            mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_bin")
        for idx in table["indexes"]:
            sa.Index(idx["name"], *(row.c[name] for name in idx["columns"]), unique=idx["unique"])
    return result
