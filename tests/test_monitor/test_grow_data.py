"""scripts/grow_data.py: làm dữ liệu nguồn to dần mà vẫn giữ kiểu bẩn như seed."""

import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

from infra.mock_api import app as mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("grow_data", ROOT / "scripts" / "grow_data.py")
grow_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grow_data)


def test_orders_sql_keeps_the_seed_dirt_rules():
    sql = grow_data.orders_sql(500)
    assert "generate_series(start.base + 1, start.base + 500)" in sql
    assert "'DD/MM/YYYY'" in sql and "'YYYY/MM/DD'" in sql and "|| '.00'" in sql
    assert sql.strip().startswith("BEGIN;") and sql.strip().endswith("COMMIT;")


def test_api_grows_in_memory_and_stays_out_of_openapi(monkeypatch):
    monkeypatch.setattr(mock, "PAYMENTS", list(mock.PAYMENTS))
    client = TestClient(mock.app)
    before = len(mock.PAYMENTS)
    assert client.post("/sales/_admin/grow", params={"first_order": 31, "orders": 5}).json()["payments"] == 20
    assert len(mock.PAYMENTS) == before + 20 and mock.PAYMENTS[-1]["order_id"] == 35
    assert "/_admin/grow" not in client.get("/sales/openapi.json").json()["paths"]
    total = client.get("/sales/payments", params={"page": 8, "page_size": 20}).json()["total"]
    assert total == before + 20


def test_new_refund_file_continues_the_ids(tmp_path):
    (tmp_path / "refunds_2026_01.csv").write_text("refund_id,order_id\n1,2\n12,29\n", encoding="utf-8")
    path = grow_data.grow_csv(31, 30, folder=tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.name == "refunds_grow_001.csv" and lines[0].startswith("refund_id,")
    assert [line.split(",")[:2] for line in lines[1:]] == [["13", "40"], ["14", "50"], ["15", "60"]]
    assert grow_data.grow_csv(61, 10, folder=tmp_path).name == "refunds_grow_002.csv"
