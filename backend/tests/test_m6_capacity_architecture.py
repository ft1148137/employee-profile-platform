"""Keep capacity independent from profile publication and ingestion."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table
from sqlmodel import SQLModel

APP = Path(__file__).resolve().parents[1] / "app"
ENTRYPOINTS = {
    "api/main.py",
    "api/routes/capacity.py",
    "domains/__init__.py",
    "mcp/server.py",
}
FORBIDDEN = {"profile", "evidence", "drafting", "source", "review", "change_request"}
EXCEPTIONS = {
    "app.domains.review.routing",
    "app.domains.change_request.schemas",
    "app.domains.source.models",
}


def imports(path: Path, relative: str) -> list[str]:
    modules = []
    package = ["app", *Path(relative).parts[:-1]]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = (
                ".".join(package[: len(package) - node.level + 1]) if node.level else ""
            )
            module = ".".join(filter(None, [prefix, node.module]))
            modules.extend(f"{module}.{a.name}" for a in node.names)
    return modules


def violations(root: Path) -> list[str]:
    errors = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        inside = relative.startswith("domains/capacity/")
        entrypoint = relative in ENTRYPOINTS or relative.startswith(
            ("domains/bot/", "domains/im/")
        )
        for module in imports(path, relative):
            if (
                not inside
                and not entrypoint
                and (
                    module == "app.domains.capacity"
                    or module.startswith("app.domains.capacity.")
                )
            ):
                errors.append(f"{relative}: {module}")
            if (
                inside
                and any(
                    module.startswith(f"app.domains.{name}.")
                    or module == f"app.domains.{name}"
                    for name in FORBIDDEN
                )
                and not any(
                    module == allowed or module.startswith(allowed + ".")
                    for allowed in EXCEPTIONS
                )
            ):
                errors.append(f"{relative}: {module}")
    return errors


def test_capacity_import_boundaries() -> None:
    assert violations(APP) == []


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("domains/profile/bad.py", "from app.domains import capacity"),
        ("domains/profile/bad.py", "import app.domains.capacity.workload as workload"),
        ("domains/capacity/bad.py", "from app.domains.review import publish"),
        ("domains/capacity/bad.py", "from ..profile.models import ProfileVersion"),
        (
            "domains/capacity/bad.py",
            "import app.domains.source.service",
        ),
    ],
)
def test_import_counterexamples(tmp_path: Path, relative: str, source: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(source)
    assert violations(tmp_path)


def foreign_key_violations(metadata: MetaData) -> list[str]:
    errors = []
    for table in metadata.tables.values():
        if not table.name.startswith("capacity_"):
            errors.extend(
                table.name
                for fk in table.foreign_keys
                if fk.column.table.name.startswith("capacity_")
            )
    return errors


def test_profile_tables_do_not_reference_capacity() -> None:
    assert foreign_key_violations(SQLModel.metadata) == []


def test_foreign_key_counterexample() -> None:
    metadata = MetaData()
    Table("capacity_probe", metadata, Column("id", Integer, primary_key=True))
    Table(
        "profile_probe",
        metadata,
        Column("capacity_id", Integer, ForeignKey("capacity_probe.id")),
    )
    assert foreign_key_violations(metadata) == ["profile_probe"]
