# SPDX-License-Identifier: MIT
"""Read synthetic CSV work records through the existing ingestion contract."""

import json
import sys
from pathlib import Path

from app.domains.source.import_records import InvalidWorkRecords, preview_work_records
from app.domains.source.sync import csv_record
from app.domains.work.ingest import SourceRecord


class SampleCsvConnector:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> list[SourceRecord]:
        parsed = preview_work_records(self.path.read_bytes(), self.path.name)
        if parsed.has_errors:
            raise InvalidWorkRecords(parsed.issues)
        return [csv_record(row) for row in parsed.rows]


if __name__ == "__main__":
    records = SampleCsvConnector(Path(__file__).with_name("work_records.csv")).read()
    sys.stdout.write(
        json.dumps(
            [r.model_dump(mode="json") for r in records], ensure_ascii=False, indent=2
        )
        + "\n"
    )
