"""Cổng budget với byte thật từ metadata Parquet trên Azure Blob Storage (thay dry_run của BigQuery).

Reader cục bộ có cùng giao diện với blob (liệt kê file, đọc theo range) và đếm số byte đã đọc, để chứng minh chỉ
footer được tải, không phải dữ liệu.
"""

import sys
from pathlib import Path

import duckdb
import pytest

from src.agents import azure_storage, budget
from src.agents.nodes.budget import budget_node
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.spec.models import Spec
from tests.test_agents.test_graph import SPEC, FakeLLM, deps

AZURE = PipelineSettings(
    _env_file=None, azure_storage_connection_string="UseDevelopmentStorage=true", azure_storage_container="sources"
)


class LocalReader:
    """Thư mục local đóng vai container; ghi lại số byte mỗi lần đọc."""

    def __init__(self, root: Path):
        self.root, self.read = root, []

    def files(self, prefix: str) -> list[tuple[str, int]]:
        folder = self.root / prefix
        found = sorted(folder.glob("*.parquet")) if folder.exists() else []
        return [(p.relative_to(self.root).as_posix(), p.stat().st_size) for p in found]

    def tail(self, name: str, size: int, length: int) -> bytes:
        self.read.append(length)
        return (self.root / name).read_bytes()[size - length :]


def _mirror(root: Path, rows: int = 50_000, extra_column: bool = True) -> Path:
    """Bản sao Parquet của sales_pg.orders: các cột spec_01 dùng, cộng một cột to không dùng."""
    spec = Spec.model_validate(SPEC)
    used = azure_storage.used_columns(spec.sources[0])
    columns = [f"'v' || range::varchar AS \"{c}\"" for c in used]
    if extra_column:
        columns.append("repeat('ghi chú không dùng ', 20) || range::varchar AS internal_note")
    path = root / "sales_pg" / "orders" / "part-0.parquet"
    path.parent.mkdir(parents=True)
    duckdb.sql(f"COPY (SELECT {', '.join(columns)} FROM range({rows})) TO '{path.as_posix()}' (FORMAT parquet)")
    return path


def test_bytes_are_the_compressed_size_of_used_columns_only(tmp_path):
    path = _mirror(tmp_path)
    reader = LocalReader(tmp_path)

    found = azure_storage.source_bytes(Spec.model_validate(SPEC), AZURE, reader)["orders"]

    expected = duckdb.sql(
        "select sum(total_compressed_size) from parquet_metadata(?) where path_in_schema <> 'internal_note'",
        params=[path.as_posix()],
    ).fetchone()[0]
    assert found["bytes"] == expected and found["bytes"] < path.stat().st_size, "cột không dùng không bị tính"
    assert found["files"] == 1 and "internal_note" not in found["columns"]
    assert sum(reader.read) == found["footer_bytes"] < path.stat().st_size / 100, "chỉ đọc footer, không đọc dữ liệu"


def test_budget_gate_uses_the_measured_bytes(tmp_path):
    _mirror(tmp_path)

    estimated = budget.estimate(Spec.model_validate(SPEC), load_policy(), AZURE, reader=LocalReader(tmp_path))

    assert estimated["estimated"] is False and estimated["measured"]["warehouse"] == "azure_blob_parquet"
    assert estimated["bytes"] == 2 * estimated["sources"]["orders"]
    gate, errors = budget.gate(estimated, load_policy(), spent=0.0)
    assert gate.result == "pass" and not errors and gate.detail.startswith("Metadata Parquet trên Azure")


def test_missing_mirror_stops_the_run_as_infra_error(tmp_path):
    def from_empty_container(spec, policy, settings):
        return budget.estimate(spec, policy, AZURE, reader=LocalReader(tmp_path))

    update = budget_node(deps(FakeLLM(), estimate_cost=from_empty_container))({"spec": SPEC, "error_trace": []})

    assert update["status"] == "INFRA_ERROR" and "azure_mirror.py" in update["error_trace"][-1]["error"]


def test_stale_mirror_without_a_used_column_is_an_error(tmp_path):
    path = tmp_path / "sales_pg" / "orders" / "part-0.parquet"
    path.parent.mkdir(parents=True)
    duckdb.sql(f"COPY (SELECT range AS order_id FROM range(10)) TO '{path.as_posix()}' (FORMAT parquet)")

    with pytest.raises(azure_storage.AzureStorageError, match="không có cột"):
        azure_storage.source_bytes(Spec.model_validate(SPEC), AZURE, LocalReader(tmp_path))


def test_a_file_that_is_not_parquet_is_rejected(tmp_path):
    path = tmp_path / "sales_pg" / "orders" / "part-0.parquet"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"day khong phai parquet")

    with pytest.raises(azure_storage.AzureStorageError, match="không phải file Parquet"):
        azure_storage.source_bytes(Spec.model_validate(SPEC), AZURE, LocalReader(tmp_path))


def test_missing_client_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.storage.blob", None)

    with pytest.raises(azure_storage.AzureStorageError, match="pip install azure-storage-blob"):
        azure_storage.BlobFooterReader("UseDevelopmentStorage=true", "sources")


def test_without_azure_settings_the_estimate_is_unchanged(monkeypatch):
    monkeypatch.setattr(budget, "_source_bytes", lambda spec, policy, settings, skip=frozenset(): {"orders": 10})

    off = PipelineSettings(_env_file=None, azure_storage_connection_string="", azure_storage_container="")
    estimated = budget.estimate(Spec.model_validate(SPEC), load_policy(), off)

    assert estimated["estimated"] is True and "measured" not in estimated and estimated["bytes"] == 20
