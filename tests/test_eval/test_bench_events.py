"""Bộ sự kiện lớn cho ca optimizer (eval/bench/make_events.py): tất định, đúng số dòng, không sinh lại khi đã đủ."""

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_events", ROOT / "eval" / "bench" / "make_events.py")
make_events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_events)


def _rows(folder: Path) -> list[dict]:
    with (folder / "events.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_defaults_target_five_million_rows_in_an_ignored_landing_folder():
    assert make_events.DEFAULT_ROWS >= 5_000_000
    assert make_events.EVENTS_DIR == ROOT / "infra" / "landing" / "bench" / "events"
    assert "/infra/landing/bench/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_same_rows_give_the_same_file(tmp_path):
    assert make_events.ensure_events(2_000, tmp_path / "a") and make_events.ensure_events(2_000, tmp_path / "b")
    first, second = (tmp_path / "a" / "events.csv").read_bytes(), (tmp_path / "b" / "events.csv").read_bytes()
    assert first == second
    assert first.startswith(b"event_id,event_date,user_id,amount\n1,2025-01-01,1,10000\n")


def test_row_count_and_value_ranges(tmp_path):
    make_events.ensure_events(10_000, tmp_path)
    rows = _rows(tmp_path)
    assert len(rows) == 10_000 == make_events.existing_rows(tmp_path / "events.csv")
    assert [r["event_id"] for r in rows] == [str(i) for i in range(1, 10_001)]
    days = {r["event_date"] for r in rows}
    assert len(days) == 365 and min(days) == "2025-01-01" and max(days) == "2025-12-31"
    users = {int(r["user_id"]) for r in rows}
    assert len(users) == 10_000 and min(users) >= 1 and max(users) <= make_events.USERS
    amounts = {int(r["amount"]) for r in rows}
    assert min(amounts) >= 10_000 and max(amounts) <= 999_000 and all(a % 1_000 == 0 for a in amounts)
    assert not list(tmp_path.glob("*.tmp")), "không để lại file ghi dở"


def test_existing_file_with_enough_rows_is_not_rewritten(tmp_path, monkeypatch):
    assert make_events.ensure_events(500, tmp_path) is True

    def boom(_i):
        raise AssertionError("không được sinh lại")

    monkeypatch.setattr(make_events, "row", boom)
    assert make_events.ensure_events(500, tmp_path) is False
    monkeypatch.undo()
    assert make_events.ensure_events(800, tmp_path) is True, "khác số dòng thì sinh lại"
    assert len(_rows(tmp_path)) == 800


def test_large_case_reads_the_generated_folder_through_the_bench_domain():
    """Ca optimizer lớn trỏ tới nguồn filesystem của domain bench, gốc là thư mục cha của nơi script ghi CSV."""
    import json

    from src.policy.loader import load_policy

    case = json.loads((ROOT / "eval" / "cases" / "optimize_large_events_by_day.json").read_text(encoding="utf-8"))
    assert case["bench_rows"] >= 3_000_000 and case["uc3_parent_patch"] == {"materialization": "table"}
    assert case["expected"]["uc3_measured"] == ["table_to_incremental"] and case["expected"]["any_applied"] is True
    assert case["expected"]["uc3_status_in"] == ["READY_FOR_REVIEW", "NO_IMPROVEMENT"]
    domains = load_policy().domains
    (source,) = case["sources"]
    assert domains.domain_of_source(source["source_ref"]) == "bench"
    assert source["tables"] == [make_events.EVENTS_DIR.name]
    decl = domains.domains["bench"].sources[source["source_ref"]]
    assert decl.kind == "filesystem" and ROOT / decl.root == make_events.EVENTS_DIR.parent
    assert domains.domains["bench"].entities["event"].tables == [f"{source['source_ref']}.events"]


def test_rejects_non_positive_rows(tmp_path):
    with pytest.raises(ValueError, match="lớn hơn 0"):
        make_events.ensure_events(0, tmp_path)
