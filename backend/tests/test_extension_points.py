from typing import get_args

from app.core.config import settings
from app.domains.capability.files import parse_dictionary
from app.domains.capability.templates import load_catalog
from app.domains.im.gateway import ADAPTERS
from app.domains.im.schemas import Platform


def test_im_platform_registration() -> None:
    assert set(get_args(Platform)) == set(ADAPTERS)
    for platform, adapter in ADAPTERS.items():
        assert adapter.platform == platform


def test_capability_template_registration() -> None:
    directory = settings.CAPABILITY_TEMPLATES_DIR
    templates = load_catalog(directory)
    assert {template.entry.file for template in templates} == {
        path.name
        for path in directory.iterdir()
        if path.suffix in {".yaml", ".yml"} and path.name != "catalog.yaml"
    }
    for template in templates:
        path = directory / template.entry.file
        definitions, errors, warnings = parse_dictionary(path.read_bytes(), path.name)
        assert not errors, (path.name, errors)
        assert not warnings, (path.name, warnings)
        assert [definition.model_dump() for definition in definitions] == [
            capability.model_dump() for capability in template.capabilities
        ]
