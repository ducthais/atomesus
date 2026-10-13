"""Domain `bench` (bộ sự kiện lớn của eval optimizer) khai báo trong domains.yml nhưng thư mục chưa sinh.

Thư mục infra/landing/bench/ chỉ có sau khi chạy eval/bench/make_events.py (đã .gitignore). Khi chưa có, catalog,
danh sách nguồn, policy và catalog của planner vẫn chạy: nguồn bench hiện `available: false` kèm lý do, không 500.
"""

from types import SimpleNamespace

import pytest

BENCH_REASON = "Không có thư mục nguồn 'infra/landing/bench'"


@pytest.fixture
def no_bench(tmp_path, monkeypatch):
    """Đăng nhập được, Postgres và API giả ở biên connector; gốc repo của nguồn file là thư mục rỗng (chưa sinh dữ liệu)."""
    from src.main import app
    from src.pipeline import auth, connectors, files, postgres, rest_api
    from src.pipeline.settings import PipelineSettings

    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    (tmp_path / "repo").mkdir()
    monkeypatch.setattr(files, "PROJECT_ROOT", (tmp_path / "repo").resolve())
    monkeypatch.setattr(
        connectors,
        "postgres_columns",
        lambda _c, schema, decl=None: {"orders": [{"name": "order_id", "type": "integer"}]},
    )
    monkeypatch.setattr(connectors, "postgres_keys", lambda _c, schema, decl=None: ({"orders": ["order_id"]}, []))
    monkeypatch.setattr(connectors, "postgres_row_estimates", lambda _c, schema, decl=None: {"orders": 30})

    def api_down(*_args, **_kwargs):
        raise postgres.SourceUnavailableError("API giả không chạy trong test.")

    monkeypatch.setattr(rest_api, "endpoints", api_down)
    yield config
    app.dependency_overrides.pop(auth.settings, None)


async def _login(client):
    await client.post("/api/v1/pipeline/session", json={"username": "engineer", "password": "Engineer@123"})


@pytest.mark.asyncio
async def test_catalog_shows_bench_unavailable_while_other_domains_work(client, no_bench):
    await _login(client)
    response = await client.get("/api/v1/catalog")
    assert response.status_code == 200
    domains = response.json()["domains"]
    assert {"sales", "marketing", "rental", "bench"} <= set(domains)
    bench = domains["bench"]["sources"]["landing_bench"]
    assert bench["available"] is False and bench["reason"].startswith(BENCH_REASON)
    assert bench["location"] == "infra/landing/bench" and bench["tables"] == {}
    assert domains["bench"]["entities"]["event"]["tables"] == ["landing_bench.events"]
    assert domains["sales"]["sources"]["sales_pg"]["available"] is True


@pytest.mark.asyncio
async def test_source_list_keeps_bench_and_discovery_answers_503(client, no_bench):
    await _login(client)
    response = await client.get("/api/v1/catalog/sources")
    assert response.status_code == 200
    sources = {s["ref"]: s for s in response.json()}
    assert sources["landing_bench"] == {
        "ref": "landing_bench",
        "domain": "bench",
        "kind": "filesystem",
        "connection_ref": "landing_bench",
        "location": "infra/landing/bench",
        "supported": True,
    }
    assert {"sales_pg", "landing_sales", "pagila_pg"} <= set(sources)
    objects = await client.get("/api/v1/catalog/sources/landing_bench/objects")
    assert objects.status_code == 503 and objects.json()["detail"].startswith(BENCH_REASON)


@pytest.mark.asyncio
async def test_policy_returns_files_with_the_bench_domain(client, no_bench):
    await _login(client)
    response = await client.get("/api/v1/policy")
    assert response.status_code == 200
    body = response.json()
    assert "landing_bench" in body["files"]["domains.yml"] and "bench:" in body["files"]["objectives.yml"]


def test_planner_catalog_marks_bench_unavailable_without_raising(no_bench):
    from src.agents import resolve
    from src.policy.loader import load_policy

    catalog = resolve.catalog(load_policy(), no_bench)
    bench = catalog["bench"]["sources"]["landing_bench"]
    assert bench == {"kind": "filesystem", "available": False, "reason": bench["reason"]}
    assert bench["reason"].startswith(BENCH_REASON)
    assert catalog["bench"]["entities"]["event"].startswith("một sự kiện")
    assert catalog["sales"]["sources"]["sales_pg"]["objects"] == {"orders": ["order_id"]}
