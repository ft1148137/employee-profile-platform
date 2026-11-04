"""Store Pi provider separately from model ID.

Revision ID: b102001bc001
Revises: a91e001bc001
"""
from alembic import op
import sqlalchemy as sa

revision = "b102001bc001"
down_revision = "a91e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_setting", sa.Column("provider", sa.String(40), nullable=True))
    table = sa.table("model_setting", sa.column("org_id"), sa.column("kind"), sa.column("model"), sa.column("enabled"), sa.column("provider"))
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings().all():
        if row["kind"] == "chatgpt_subscription" or not row["model"]:
            continue
        prefix, separator, model_id = row["model"].partition("/")
        provider = {"openai": "openai", "anthropic": "anthropic", "deepseek": "deepseek", "openrouter": "openrouter", "ollama": "ollama", "ollama_chat": "ollama"}.get(prefix)
        if row["kind"] == "openai_compatible":
            provider = "openai-compatible"
        values = {"provider": provider, "model": model_id} if provider and separator and model_id else {"enabled": False}
        connection.execute(table.update().where(table.c.org_id == row["org_id"]).values(**values))


def downgrade() -> None:
    table = sa.table("model_setting", sa.column("org_id"), sa.column("model"), sa.column("provider"), sa.column("enabled"))
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings().all():
        if row["provider"] and row["model"]:
            prefix = {"openai-compatible": "openai", "ollama": "ollama_chat"}.get(row["provider"], row["provider"])
            model = f"{prefix}/{row['model']}"
            if len(model) > 200 and row["provider"] == "ollama":
                model = f"ollama/{row['model']}"
            values = {"model": model} if len(model) <= 200 else {"model": row["model"], "enabled": False}
            connection.execute(table.update().where(table.c.org_id == row["org_id"]).values(**values))
    op.drop_column("model_setting", "provider")
