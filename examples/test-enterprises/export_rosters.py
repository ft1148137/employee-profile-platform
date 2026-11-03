"""Export synthetic active employees as browser-uploadable M1 rosters."""

import csv
from io import StringIO
from pathlib import Path

import yaml
from app.domains.org.roster.parser import parse_roster
from app.domains.org.roster.schemas import HEADERS

DATA = Path(__file__).resolve().parent


def roster_csv(path: Path) -> bytes:
    org = yaml.safe_load((path / "org.yaml").read_text(encoding="utf-8"))
    roster = yaml.safe_load((path / "roster.yaml").read_text(encoding="utf-8"))
    active = {
        entry["employee_code"]: entry for entry in roster if entry["status"] == "active"
    }
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADERS)
    for employee in org["employees"]:
        code = employee["employee_code"]
        if code in active:
            writer.writerow(
                [
                    code,
                    employee["display_name"],
                    employee["department"],
                    employee["title"],
                    active[code]["reports_to"] or "",
                    employee["work_email"],
                ]
            )
    content = output.getvalue().encode("utf-8-sig")
    result = parse_roster(content, "roster.csv")
    if result.has_errors:
        raise ValueError(f"{path.name}: {result.issues}")
    assert len(result.rows) == len(active), f"{path.name}: missing active employees"
    assert all(row.department_path[0] in org["departments"] for row in result.rows)
    return content


def main() -> None:
    for path in sorted(DATA.glob("*/org.yaml")):
        content = roster_csv(path.parent)
        (path.parent / "roster.csv").write_bytes(content)
        print(f"{path.parent.name}: exported roster.csv")


if __name__ == "__main__":
    main()
