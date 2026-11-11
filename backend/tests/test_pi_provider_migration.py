from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlmodel import Session

from app.domains.org.models import Organization, OrgMembership
from app.models import User
from tests.utils.migrations import insert_historical_rows, migrate


def test_provider_migration_roundtrip(migration_engine: Engine) -> None:
    migrate(migration_engine, "downgrade", "a91e001bc001")
    cases = [
        ("cloud_api", "openai/gpt-4o-mini", "openai", "gpt-4o-mini"),
        ("cloud_api", "anthropic/claude", "anthropic", "claude"),
        ("cloud_api", "deepseek/deepseek-chat", "deepseek", "deepseek-chat"),
        ("cloud_api", "openrouter/vendor/model", "openrouter", "vendor/model"),
        ("local", "ollama/llama3", "ollama", "llama3"),
        ("local", "ollama_chat/llama3", "ollama", "llama3"),
        ("local", "ollama/" + "x" * 193, "ollama", "x" * 193),
        (
            "openai_compatible",
            "openai/custom/model",
            "openai-compatible",
            "custom/model",
        ),
        ("openai_compatible", "openai/" + "x" * 193, "openai-compatible", "x" * 193),
        ("openai_compatible", "x/" + "y" * 198, "openai-compatible", "y" * 198),
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
            insert_historical_rows(session, org)
            session.add(user)
            session.flush()
            session.add(OrgMembership(org_id=org.id, user_id=user.id))
            session.flush()
            session.execute(
                table.insert().values(
                    org_id=org.id,
                    kind=kind,
                    model=model,
                    enabled=not model.startswith("x/"),
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
        for org_id, (kind, original, provider, model) in zip(ids, cases, strict=True):
            row = (
                connection.execute(sa.select(table).where(table.c.org_id == org_id))
                .mappings()
                .one()
            )
            assert row["provider"] == provider and row["model"] == model
            assert row["enabled"] == (
                not original.startswith("x/")
                and (provider is not None or kind == "chatgpt_subscription")
            )
            assert row["api_key_ciphertext"] == "synthetic-ciphertext"
    migrate(migration_engine, "downgrade", "a91e001bc001")
    with migration_engine.connect() as connection:
        table = sa.Table("model_setting", sa.MetaData(), autoload_with=connection)
        assert "provider" not in table.c
        for org_id, (kind, original, provider, model) in zip(ids, cases, strict=True):
            row = (
                connection.execute(sa.select(table).where(table.c.org_id == org_id))
                .mappings()
                .one()
            )
            expected = (
                original.replace("ollama/", "ollama_chat/", 1)
                if provider == "ollama" and len("ollama_chat/" + model) <= 200
                else original
            )
            if original.startswith("x/"):
                expected = model
            assert row["enabled"] == (
                not original.startswith("x/")
                and (provider is not None or kind == "chatgpt_subscription")
            )
            assert row["model"] == expected
            assert row["api_key_ciphertext"] == "synthetic-ciphertext"
    migrate(migration_engine, "upgrade", "head")
