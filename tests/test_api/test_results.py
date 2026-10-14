"""Xem và tải bảng gold (ADR-0001 D9): phân trang, sắp xếp, CSV/XLSX, quyền theo chủ tên model."""

import csv
import io
import time
import zipfile
from types import SimpleNamespace

import duckdb
import pytest
import pytest_asyncio

from src.api import results
from src.main import app
from src.pipeline import api, auth, store
from src.pipeline.settings import PipelineSettings
from src.policy import load_policy

PASSWORD = "StrongPass9"
USERS = {"eng.a": "Engineer", "eng.b": "Engineer", "rev.one": "Reviewer"}
ORDERS = [
    (1, "Hà Nội", 120.5),
    (2, "Đà Nẵng", 80.0),
    (3, "Huế", None),
    (4, "Cần Thơ", 300.25),
    (5, "Hải Phòng", 45.0),
    (6, "Nha Trang", 210.0),
    (7, "Vũng Tàu", 99.9),
]


@pytest_asyncio.fixture
async def env(client, tmp_path, monkeypatch):
    """Engineer A giữ tên a_model (có bảng gold) và ghost (chưa có bảng); B và Reviewer chưa liên kết."""
    warehouse = tmp_path / "warehouse.duckdb"
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"), warehouse_path=str(warehouse))
    environment = SimpleNamespace(app_env="test")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    ids = {
        username: store.create_user(
            config.store_path,
            user_id=f"user_{username.replace('.', '_')}",
            username=username,
            name=username,
            role=role,
            password_hash=auth.hash_password(PASSWORD),
            created_at=time.time(),
            email=f"{username}@example.com",
        )["id"]
        for username, role in USERS.items()
    }
    for model in ("a_model", "ghost", "big_model"):
        store.claim_model(config.store_path, model, ids["eng.a"], time.time())
    with duckdb.connect(str(warehouse)) as db:
        db.execute("CREATE SCHEMA gold")
        db.execute("CREATE TABLE gold.a_model (id INTEGER, city VARCHAR, amount DOUBLE)")
        db.executemany("INSERT INTO gold.a_model VALUES (?, ?, ?)", ORDERS)
        cap = load_policy().budgets.results.max_export_rows
        db.execute(f"CREATE TABLE gold.big_model AS SELECT range AS id FROM range({cap + 1})")
    app.dependency_overrides[auth.settings] = lambda: config
    try:
        yield SimpleNamespace(client=client, config=config, ids=ids)
    finally:
        app.dependency_overrides.pop(auth.settings, None)


async def _login(env, username: str, password: str = PASSWORD) -> None:
    await env.client.delete("/api/v1/pipeline/session")
    response = await env.client.post("/api/v1/pipeline/session", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_pages_are_sorted_by_real_columns_and_page_size_is_capped(env):
    await _login(env, "eng.a")
    client = env.client

    first = await client.get("/api/v1/results/a_model", params={"page_size": 3})
    assert first.status_code == 200, first.text
    body = first.json()
    assert [c["name"] for c in body["columns"]] == ["id", "city", "amount"]
    assert body["total"] == 7 and body["page"] == 1
    assert body["rows"] == [[1, "Hà Nội", 120.5], [2, "Đà Nẵng", 80.0], [3, "Huế", None]]

    by_amount = await client.get(
        "/api/v1/results/a_model", params={"page": 2, "page_size": 3, "sort": "amount", "order": "desc"}
    )
    # 300.25, 210, 120.5 | 99.9, 80, 45 | NULL (luôn cuối)
    assert [row[0] for row in by_amount.json()["rows"]] == [7, 2, 5]
    last = await client.get("/api/v1/results/a_model", params={"page": 3, "page_size": 3, "sort": "amount"})
    assert [row[0] for row in last.json()["rows"]] == [3]
    beyond = await client.get("/api/v1/results/a_model", params={"page": 9, "page_size": 3})
    assert beyond.json()["rows"] == [] and beyond.json()["total"] == 7

    assert (await client.get("/api/v1/results/a_model", params={"page_size": 200})).status_code == 200
    assert (await client.get("/api/v1/results/a_model", params={"page_size": 201})).status_code == 422
    assert (await client.get("/api/v1/results/a_model", params={"order": "sideways"})).status_code == 422


@pytest.mark.asyncio
async def test_unknown_sort_column_is_422_and_never_reaches_sql(env):
    await _login(env, "eng.a")
    for sort in ("price", 'amount" DESC; DROP TABLE gold.a_model; --', "1"):
        response = await env.client.get("/api/v1/results/a_model", params={"sort": sort})
        assert response.status_code == 422, sort
        assert "Không có cột" in response.json()["detail"]
        assert (await env.client.get("/api/v1/results/a_model/export", params={"sort": sort})).status_code == 422
    assert (await env.client.get("/api/v1/results/a_model")).json()["total"] == 7


@pytest.mark.asyncio
async def test_strange_or_missing_model_is_404(env):
    await _login(env, "eng.a")
    for model in ('a_model"; DROP TABLE gold.a_model; --', "a-model", "gold.a_model", "1model", "x" * 64, "ghost"):
        for suffix in ("", "/export"):
            response = await env.client.get(f"/api/v1/results/{model}{suffix}")
            assert response.status_code == 404, (model, suffix)
    # Tên chưa ai giữ là dữ liệu cũ: Engineer thường không thấy, kể cả khi không có bảng.
    assert (await env.client.get("/api/v1/results/unclaimed")).status_code == 404


@pytest.mark.asyncio
async def test_missing_warehouse_is_404(env):
    env.config.warehouse_path = str(env.config.store_path) + ".missing.duckdb"
    await _login(env, "eng.a")
    assert (await env.client.get("/api/v1/results/a_model")).status_code == 404


@pytest.mark.asyncio
async def test_others_get_404_linked_reviewer_and_admin_can_read(env):
    client = env.client
    for username in ("eng.b", "rev.one"):
        await _login(env, username)
        for path in ("/api/v1/results/a_model", "/api/v1/results/a_model/export"):
            response = await client.get(path)
            assert response.status_code == 404, (username, path)
            assert response.json()["detail"] == "Không có kết quả gold.a_model mà bạn được xem."
        # Không lộ cột qua thông báo sắp xếp.
        assert (await client.get("/api/v1/results/a_model", params={"sort": "nope"})).status_code == 404

    await _login(env, "eng.a")
    invite = await client.post("/api/v1/pipeline/invites")
    assert invite.status_code == 201, invite.text
    await _login(env, "rev.one")
    accepted = await client.post("/api/v1/pipeline/invites/accept", json={"token": invite.json()["token"]})
    assert accepted.status_code == 200, accepted.text
    assert (await client.get("/api/v1/results/a_model")).json()["total"] == 7
    assert (await client.get("/api/v1/results/a_model/export")).status_code == 200

    await _login(env, "admin", "Admin@123")
    assert (await client.get("/api/v1/results/a_model")).json()["total"] == 7


@pytest.mark.asyncio
async def test_csv_has_bom_all_rows_and_model_file_name(env):
    await _login(env, "eng.a")
    response = await env.client.get("/api/v1/results/a_model/export", params={"format": "csv", "sort": "id"})
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-disposition"] == 'attachment; filename="a_model.csv"'
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0] == ["id", "city", "amount"]
    assert len(rows) == 1 + len(ORDERS)
    assert rows[1] == ["1", "Hà Nội", "120.5"] and rows[3] == ["3", "Huế", ""]


@pytest.mark.asyncio
async def test_xlsx_is_a_workbook_with_every_row(env):
    await _login(env, "eng.a")
    response = await env.client.get("/api/v1/results/a_model/export", params={"format": "xlsx"})
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="a_model.xlsx"'
    with zipfile.ZipFile(io.BytesIO(response.content)) as book:
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"} <= set(book.namelist())
        sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert sheet.count("<row ") == 1 + len(ORDERS)
    assert '<c r="B2" t="inlineStr"><is><t xml:space="preserve">Hà Nội</t></is></c>' in sheet
    assert '<c r="C5"><v>300.25</v></c>' in sheet
    assert (await env.client.get("/api/v1/results/a_model/export", params={"format": "pdf"})).status_code == 422


@pytest.mark.asyncio
async def test_export_over_policy_cap_is_413(env):
    cap = load_policy().budgets.results.max_export_rows
    assert cap == 100_000  # đọc từ policy/budgets.yml
    await _login(env, "eng.a")
    response = await env.client.get("/api/v1/results/big_model/export")
    assert response.status_code == 413
    assert f"vượt trần xuất {cap} dòng" in response.json()["detail"]
    # Vẫn xem từng trang được.
    assert (await env.client.get("/api/v1/results/big_model")).json()["total"] == cap + 1


@pytest.mark.asyncio
async def test_locked_warehouse_is_503_in_vietnamese(env, monkeypatch):
    def locked(*_args, **_kwargs):
        raise duckdb.IOException('IO Error: Could not set lock on file "warehouse.duckdb": Conflicting lock is held')

    monkeypatch.setattr(results.duckdb, "connect", locked)
    await _login(env, "eng.a")
    for path in ("/api/v1/results/a_model", "/api/v1/results/a_model/export"):
        response = await env.client.get(path)
        assert response.status_code == 503, path
        assert response.json()["detail"] == "Pipeline đang chạy, thử lại sau ít phút."
