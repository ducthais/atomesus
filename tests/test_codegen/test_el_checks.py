"""Đối soát E-L đọc nguồn file / API theo luồng: đếm và gom tập khóa mà không giữ mọi dòng trong RAM."""

import weakref
from types import SimpleNamespace

from src.codegen import el_checks
from src.pipeline import files
from src.policy.loader import load_policy


def _source(primary_key):
    return SimpleNamespace(kind="filesystem", path_pattern="refunds/*.csv", primary_key=primary_key, staging=None)


def test_filesystem_side_counts_records_and_distinct_keys_like_before():
    decl = load_policy().domains.domains["sales"].sources["landing_sales"]
    rows = list(files.records(decl, "refunds"))  # infra/landing/sales: khoản hoàn 7 xuất lặp ở hai file
    side = el_checks.read_source(_source(["refund_id"]), decl, None)
    assert side.records == len(rows) == 13
    assert side.keys == {(row["refund_id"],) for row in rows} and len(side.keys) == 12


class _Row(dict):
    """dict có weakref để kiểm tra dòng cũ đã được giải phóng."""


def test_source_rows_are_not_kept_in_memory(monkeypatch):
    refs = []

    def stream(_source, _decl, _settings):
        for i in range(5):
            if i >= 2:  # vòng lặp của read_source chỉ còn giữ dòng ngay trước; dòng trước nữa phải đã được giải phóng
                assert refs[i - 2]() is None, f"dòng {i - 2} vẫn bị giữ trong RAM"
            row = _Row(event_id=str(i % 4), amount=None)
            refs.append(weakref.ref(row))
            yield row
            del row

    monkeypatch.setattr(el_checks, "source_records", stream)
    side = el_checks.read_source(_source(["event_id"]), None, None)
    assert side.records == 5 and side.keys == {("0",), ("1",), ("2",), ("3",)}
