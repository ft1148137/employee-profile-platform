import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from app.core.config import settings
from app.domains.capability.templates import InvalidTemplate, load_catalog
from app.seed.schema import Capability, read_file


def test_all_templates() -> None:
    directory = settings.CAPABILITY_TEMPLATES_DIR
    templates = load_catalog(directory)
    assert len(templates) == 11
    assert templates[0].entry.id == "tech-rnd"
    assert [t.entry.id for t in templates if t.entry.recommended] == ["tech-rnd"]
    assert {t.entry.file for t in templates} == {
        p.name for p in directory.glob("*.zh-CN.yaml")
    }
    for template in templates:
        content = (directory / template.entry.file).read_bytes()
        assert template.template_ref == (
            f"{template.entry.id}@{hashlib.sha256(content).hexdigest()[:12]}"
        )
        assert template.capabilities == read_file(
            directory / template.entry.file, list[Capability]
        )
        assert len({c.key for c in template.capabilities}) == len(template.capabilities)
        for capability in template.capabilities:
            assert [level.key for level in capability.level_rubric.levels] == [
                "needs_support",
                "routine",
                "independent_complex",
            ]


@pytest.mark.parametrize(
    "problem",
    [
        "extra_file",
        "missing_file",
        "missing_catalog",
        "duplicate_id",
        "invalid_file",
        "duplicate_key",
        "missing_level",
        "duplicate_level",
        "unknown_level",
        "invalid_yaml",
        "invalid_shape",
        "invalid_encoding",
    ],
)
def test_invalid_templates(tmp_path: Path, problem: str) -> None:
    directory = tmp_path / "capabilities"
    shutil.copytree(settings.CAPABILITY_TEMPLATES_DIR, directory)
    catalog = directory / "catalog.yaml"
    template = directory / "tech-rnd.zh-CN.yaml"
    entries = yaml.safe_load(catalog.read_text())
    rows = yaml.safe_load(template.read_text())
    expected_file = template.name
    if problem == "extra_file":
        (directory / "extra.zh-CN.yaml").write_text("[]")
        expected_file = "extra.zh-CN.yaml"
    elif problem == "missing_file":
        template.unlink()
    elif problem == "missing_catalog":
        catalog.unlink()
        expected_file = catalog.name
    elif problem in {"duplicate_id", "invalid_file"}:
        if problem == "duplicate_id":
            entries.append(entries[0])
        else:
            entries[0]["file"] = "../tech-rnd.zh-CN.yaml"
        catalog.write_text(yaml.safe_dump(entries))
        expected_file = catalog.name
    elif problem == "invalid_yaml":
        template.write_text("[")
    elif problem == "invalid_shape":
        template.write_text("{}")
    elif problem == "invalid_encoding":
        template.write_bytes(b"\xff")
    else:
        if problem == "duplicate_key":
            rows.append(rows[0])
        else:
            levels = rows[0]["level_rubric"]["levels"]
            if problem == "missing_level":
                levels.pop()
            elif problem == "duplicate_level":
                levels[2] = levels[0]
            else:
                levels[0]["key"] = "unknown"
        template.write_text(yaml.safe_dump(rows))
    with pytest.raises(InvalidTemplate, match=expected_file):
        load_catalog(directory)


def test_recommended_sort_preserves_catalog_order(tmp_path: Path) -> None:
    shutil.copytree(settings.CAPABILITY_TEMPLATES_DIR, tmp_path, dirs_exist_ok=True)
    catalog = tmp_path / "catalog.yaml"
    entries = yaml.safe_load(catalog.read_text())
    entries.append(entries.pop(0))
    catalog.write_text(yaml.safe_dump(entries))
    templates = load_catalog(tmp_path)
    assert templates[0].entry.id == "tech-rnd"
    assert [t.entry.id for t in templates[1:]] == [e["id"] for e in entries[:-1]]
