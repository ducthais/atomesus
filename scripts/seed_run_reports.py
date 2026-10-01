"""Nạp các RunReport mẫu (fixtures/run_reports) vào sổ bằng chứng SQLite để demo UI.

Chạy lại không nhân đôi: mỗi run_id chỉ có một dòng. Đường dẫn lấy từ PIPELINE_STORE_PATH.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline.settings import PipelineSettings  # noqa: E402
from src.run_report import FIXTURES_DIR, RunReport, save_run_report  # noqa: E402


def main():
    path = PipelineSettings().store_path
    for fixture in sorted(FIXTURES_DIR.glob("*.json")):
        report = RunReport.model_validate_json(fixture.read_text(encoding="utf-8"))
        save_run_report(path, report)
        print(f"{report.status:<28} {report.run_id}  <- {fixture.name}")
    print(f"Seeded {path}")


if __name__ == "__main__":
    main()
