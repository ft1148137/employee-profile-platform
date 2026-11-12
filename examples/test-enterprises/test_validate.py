"""Run with uv run --project backend python examples/test-enterprises/test_validate.py."""

import unittest

from pydantic import ValidationError
from validate import DATA, DepartmentNote, check, load


class DepartmentCapabilitiesTest(unittest.TestCase):
    def test_required_list_and_limit(self) -> None:
        fields = {
            "name": "综合管理部",
            "pcf_categories": ["7.0 Develop and Manage Human Capital"],
            "responsibilities": "人力与培训档案",
        }
        with self.assertRaises(ValidationError):
            DepartmentNote.model_validate(fields)
        for keys in ([], [f"key_{i}" for i in range(8)]):
            department = DepartmentNote.model_validate(
                fields | {"capability_keys": keys}
            )
            self.assertEqual(department.capability_keys, keys)
        for invalid in (None, "schematic_design", [f"key_{i}" for i in range(9)]):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                DepartmentNote.model_validate(fields | {"capability_keys": invalid})

    def test_empty_support_department_is_valid(self) -> None:
        data = load(DATA / "qiguang-aiot")
        self.assertEqual(data.enterprise.departments[-1].capability_keys, [])
        self.assertEqual(check(data), [])

    def test_duplicate_and_unknown_keys_are_rejected(self) -> None:
        for keys, expected in (
            (
                ["schematic_design", "schematic_design"],
                "duplicate 硬件研发部 capability key",
            ),
            (["nonexistent_capability"], "unknown capability_keys"),
            (["backend_api"], "unknown capability_keys"),
        ):
            with self.subTest(keys=keys):
                data = load(DATA / "qiguang-aiot")
                data.enterprise.departments[0].capability_keys = keys
                self.assertTrue(any(expected in error for error in check(data)))


if __name__ == "__main__":
    unittest.main()
