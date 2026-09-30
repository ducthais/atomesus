"""Kho RunReport mẫu (mock) — đọc từ fixtures/run_reports/*.json.

Sẽ được thay bằng sổ bằng chứng thật; API chỉ phụ thuộc vào hai hàm dưới đây.
"""

from functools import lru_cache
from pathlib import Path

from src.models.run_report import RunReport, RunStatus

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "run_reports"


@lru_cache
def _load_all() -> tuple[RunReport, ...]:
    reports = tuple(
        RunReport.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES_DIR.glob("*.json"))
    )
    run_ids = [r.run_id for r in reports]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError(f"run_id bị trùng trong {FIXTURES_DIR}")
    return tuple(sorted(reports, key=lambda r: r.created_at, reverse=True))


def list_run_reports(status: RunStatus | None = None) -> list[RunReport]:
    """Lịch sử run, mới nhất trước, lọc theo status nếu có."""
    return [r for r in _load_all() if status is None or r.status == status]


def get_run_report(run_id: str) -> RunReport | None:
    return next((r for r in _load_all() if r.run_id == run_id), None)
