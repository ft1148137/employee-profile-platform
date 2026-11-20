from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from psycopg import ClientCursor, Connection, sql
from psycopg.types.json import Jsonb
from pydantic import JsonValue
from sqlmodel import Session, col, select

from app.domains.capability.models import CapabilityDefinition, CapabilitySchemaVersion
from app.domains.org.models import (
    Department,
    Employee,
    Organization,
    OrgMembership,
    RoleGrant,
)
from app.models import User
from tests.utils.org import ORG_MODELS

Row = dict[str, JsonValue]

MINIMAL_TABLES = {
    str(model.__tablename__)
    for model in (
        Organization,
        Department,
        Employee,
        OrgMembership,
        RoleGrant,
        CapabilitySchemaVersion,
        CapabilityDefinition,
    )
}


def _remap(value: JsonValue, ids: dict[str, str]) -> JsonValue:
    # Some seed references are stored inside JSONB snapshots, audit and provenance.
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, dict):
        return {ids.get(k, k): _remap(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap(v, ids) for v in value]
    return value


@dataclass(frozen=True)
class DemoSeed:
    tables: dict[str, list[Row]]
    users: dict[str, Row]

    @classmethod
    def capture(cls, session: Session) -> DemoSeed:
        org_id = session.exec(
            select(Organization.id).where(Organization.slug == "demo")
        ).one()
        connection = session.connection().connection.driver_connection
        assert isinstance(connection, Connection)
        tables = {}
        with connection.cursor() as cursor:
            # Reverse the cleanup order so each insert can resolve its foreign keys.
            for model in (Organization, *reversed(ORG_MODELS)):
                table = str(model.__tablename__)
                query = sql.SQL(
                    "SELECT to_jsonb(seed_row) FROM {} AS seed_row WHERE {} = %s"
                )
                cursor.execute(
                    query.format(
                        sql.Identifier(table),
                        sql.Identifier("id" if model is Organization else "org_id"),
                    ),
                    (org_id,),
                )
                if rows := cursor.fetchall():
                    tables[table] = [row for (row,) in rows]
        users = session.exec(
            select(User)
            .join(OrgMembership, col(OrgMembership.user_id) == col(User.id))
            .where(OrgMembership.org_id == org_id)
        ).all()
        return cls(
            tables, {user.username: user.model_dump(mode="json") for user in users}
        )

    def clone(
        self, session: Session, *, minimal: bool = False, local_accounts: bool = False
    ) -> tuple[UUID, dict[str, UUID]]:
        org_id = uuid4()
        source = {
            table: rows
            for table, rows in self.tables.items()
            if not minimal or table in MINIMAL_TABLES
        }
        ids = {
            old: str(uuid5(org_id, old))
            for rows in source.values()
            for row in rows
            if isinstance(old := row.get("id"), str)
        }
        original_org = self.tables["organization"][0]["id"]
        assert isinstance(original_org, str)
        ids[original_org] = str(org_id)
        tables = {}
        if local_accounts:
            users = []
            for username, account in self.users.items():
                if account["is_superuser"]:
                    continue
                old_id = str(account["id"])
                new_id = ids[old_id] = str(uuid5(org_id, old_id))
                email = f"{new_id}@example.com"
                ids[username] = email
                users.append(
                    {**account, "id": new_id, "email": email, "username": email}
                )
            tables["user"] = users

        for table, rows in source.items():
            tables[table] = [
                {column: _remap(value, ids) for column, value in row.items()}
                for row in rows
            ]
        tables["organization"][0]["slug"] = f"seed-test-{org_id}"
        if local_accounts:
            for row in tables["employee"]:
                row["work_email"] = f"{row['id']}@example.com"

        connection = session.connection().connection.driver_connection
        assert isinstance(connection, Connection)
        insert = sql.SQL(
            "INSERT INTO {} SELECT * FROM jsonb_populate_recordset(NULL::{}, %s)"
        )
        with ClientCursor(connection) as cursor:
            cursor.execute(
                sql.SQL("; ").join(
                    insert.format(sql.Identifier(table), sql.Identifier(table))
                    for table in tables
                ),
                tuple(Jsonb(rows) for rows in tables.values()),
            )
        return org_id, {
            username: UUID(ids.get(old_id, old_id))
            for username, account in self.users.items()
            if isinstance(old_id := account["id"], str)
        }
