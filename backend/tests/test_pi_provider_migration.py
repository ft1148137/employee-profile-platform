from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlmodel import Session

from app.domains.org.models import Organization, OrgMembership
from app.models import User
from tests.utils.migrations import migrate


def test_provider_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a89e001bc001")
    cases = [
        ("cloud_api", "openai/gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("cloud_api", "anthropic/claude", "anthropic", "claude"),
        ("cloud_api", "deepseek/deepseek-chat", "deepseek", "deepseek-chat"),
        ("cloud_api", "openrouter/vendor/model", "openrouter", "vendor/model"),
        ("local", "ollama/llama3", "ollama", "llama3"),
        ("local", "ollama_chat/llama3", "ollama", "llama3"),
        (
            "openai_compatible",
            "openai/custom/model",
            "openai-compatible",
            "custom/model",
        ),
        ("cloud_api", "unknown/model", None, "unknown/model"),
        ("chatgpt_subscription", "chatgpt/gpt-6.1-sol", None, "chatgpt/gpt-6.1-sol"),
    ]
    ids = []
    with Session(migration_engine) as session:
        table = sa.Table(
            "model_setting", sa.MetaData(), autoload_with=session.connection()
        )
        for index, (kind, model, _, _) in enumerate(cases):
            org = Organization(slug=f"pi-migration-{index}", name="合成企业")
            user = User(
                username=f"pi-{uuid4()}@example.com", hashed_password="synthetic-unused"
            )
            session.add_all([org, user])
            session.flush()
            session.add(OrgMembership(org_id=org.id, user_id=user.id))
            session.flush()
            session.execute(
                table.insert().values(
                    org_id=org.id,
                    kind=kind,
                    model=model,
                    enabled=True,
                    api_key_ciphertext="synthetic-ciphertext",
                    updated_by_user_id=user.id,
                    updated_at=sa.func.now(),
                )
            )
            ids.append(org.id)
        session.commit()
    migrate(migration_engine, "upgrade", "head")
    with migration_engine.connect() as connection:
        table = sa.Table("model_setting", sa.MetaData(), autoload_with=connection)
        for org_id, (kind, _, provider, model) in zip(ids, cases, strict=True):
            row = (
                connection.execute(sa.select(table).where(table.c.org_id == org_id))
                .mappings()
                .one()
            )
            assert row["provider"] == provider and row["model"] == model
            assert row["enabled"] == (
                provider is not None or kind == "chatgpt_subscription"
            )
            assert row["api_key_ciphertext"] == "synthetic-ciphertext"
    migrate(migration_engine, "downgrade", "a89e001bc001")
    with migration_engine.connect() as connection:
        table = sa.Table("model_setting", sa.MetaData(), autoload_with=connection)
        assert "provider" not in table.c
        for org_id, (_, original, provider, _) in zip(ids, cases, strict=True):
            restored = connection.execute(
                sa.select(table.c.model).where(table.c.org_id == org_id)
            ).scalar_one()
            expected = (
                original.replace("ollama/", "ollama_chat/", 1)
                if provider == "ollama"
                else original
            )
            assert restored == expected
    migrate(migration_engine, "upgrade", "head")
