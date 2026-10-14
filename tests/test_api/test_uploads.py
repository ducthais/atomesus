import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from src.main import app
from src.pipeline import auth, files, store, uploads
from src.pipeline.settings import PipelineSettings
from src.policy import effective_policy, load_policy
from src.policy.models import UploadsPolicy
from src.run_report import RunReport, save_run_report
from src.run_report.access import policy_for

CSV = b"id,amount\n1,120\n2,340\n"


def workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Đơn hàng"
    sheet.append(["id", "amount"])
    sheet.append([1, 120])
    customers = workbook.create_sheet("Khách hàng")
    customers.append(["id", "name", "city"])
    customers.append([1, "Lan", "Huế"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    state = SimpleNamespace(config=config, user={"id": "minh", "role": "Engineer"}, root=tmp_path / "repo")
    monkeypatch.setattr(files, "PROJECT_ROOT", state.root)
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[auth.current_user] = lambda: state.user
    yield state
    app.dependency_overrides.pop(auth.settings, None)
    app.dependency_overrides.pop(auth.current_user, None)


def limits(monkeypatch, *, file_bytes, total_bytes):
    policy = load_policy()
    budget = policy.budgets.model_copy(
        update={"uploads": UploadsPolicy(max_file_mb=file_bytes / 1024**2, max_total_mb_per_user=total_bytes / 1024**2)}
    )
    monkeypatch.setattr(uploads, "load_policy", lambda: policy.model_copy(update={"budgets": budget}))


async def upload(client, name="orders.csv", data=CSV):
    return await client.post("/api/v1/uploads", files=[("files", (name, data, "application/octet-stream"))])


@pytest.mark.asyncio
async def test_upload_bom_csv_multiple_files_and_selection_feed_filesystem(client, registry):
    response = await client.post(
        "/api/v1/uploads",
        files=[("files", ("Đơn hàng.csv", b"\xef\xbb\xbf" + CSV)), ("files", ("Khách hàng.csv", b"id,name\n1,Lan\n"))],
    )
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert metadata["owner_id"] == "minh"
    assert metadata["selected_tables"] == []
    assert [(t["name"], t["column_count"], t["row_count"]) for t in metadata["tables"]] == [
        ("don_hang", 2, 2),
        ("khach_hang", 2, 1),
    ]
    directory = registry.root / metadata["root"]
    assert directory == registry.root / "data" / "uploads" / "minh" / metadata["id"]
    assert (directory / "don_hang" / "data.csv").read_bytes().startswith(b"id,amount")
    assert (await client.get("/api/v1/uploads")).json() == [metadata]
    response = await client.put(f"/api/v1/uploads/{metadata['id']}/selection", json={"tables": ["don_hang"]})
    assert response.status_code == 200, response.text
    selection = response.json()
    policy = effective_policy(registry.config, owner_id="minh")
    domain = policy.domains.domains[selection["domain"]]
    decl = domain.sources[selection["source_ref"]]
    assert domain.owner_id == "minh" and domain.label == "Đơn hàng.csv, Khách hàng.csv"
    assert decl.kind == "filesystem" and decl.root == metadata["root"]
    assert decl.tables == ["don_hang"] and decl.connection_ref == f"upload:{metadata['id']}"
    assert set(files.objects(decl)) == {"don_hang"}
    assert files.sample(decl, "don_hang", 20) == [{"id": "1", "amount": "120"}, {"id": "2", "amount": "340"}]
    with pytest.raises(files.FilesUnavailableError):
        files.sample(decl, "khach_hang", 1)
    with pytest.raises(files.FilesUnavailableError):
        files.sample(decl, "../khach_hang", 1)
    source = await client.get(f"/api/v1/catalog/sources/{selection['source_ref']}/objects")
    assert source.status_code == 200
    assert [obj["name"] for obj in source.json()["objects"]] == ["don_hang"]
    assert selection["domain"] in (await client.get("/api/v1/catalog")).json()["domains"]
    assert selection["source_ref"] in {item["ref"] for item in (await client.get("/api/v1/catalog/sources")).json()}
    assert (await client.get("/api/v1/uploads/limits")).json()["used_bytes"] == metadata["size_bytes"]


@pytest.mark.asyncio
async def test_xlsx_each_sheet_becomes_csv_and_names_do_not_collide(client, registry):
    response = await upload(client, "báo cáo.xlsx", workbook_bytes())
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert [(table["name"], table["column_count"], table["row_count"]) for table in metadata["tables"]] == [
        ("don_hang", 2, 1),
        ("khach_hang", 3, 1),
    ]
    assert (registry.root / metadata["root"] / "khach_hang" / "data.csv").read_text(encoding="utf-8").splitlines() == [
        "id,name,city",
        "1,Lan,Huế",
    ]
    response = await client.post(
        "/api/v1/uploads", files=[("files", ("A B.csv", CSV)), ("files", ("A-B.csv", CSV)), ("files", ("CON.csv", CSV))]
    )
    assert response.status_code == 201, response.text
    assert [table["name"] for table in response.json()["tables"]] == ["a_b", "a_b_2", "t_con"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,data",
    [
        ("fake.xlsx", CSV),
        ("fake.csv", b'{"id": 1}'),
        ("fake.csv", b"<html><body>fake</body></html>"),
        ("fake.csv", b"%PDF-1.4\ntext\n"),
        ("fake.csv", b"\x89PNG\r\n\x1a\n"),
        ("fake.csv", b"PK\x03\x04\x00binary"),
        ("bad.csv", b"id,name\n1,\xff\n"),
        ("empty.csv", b""),
        ("bad.csv", b",amount\n1,2\n"),
        ("bad.csv", b"id,id\n1,2\n"),
        ("bad.csv", b"id,amount\n1,2,3\n"),
        ("bad.csv", b'id,amount\n1,"unclosed\n'),
        ("bad.csv", b"id,amount\n1,\x00\n"),
        ("fake.txt", CSV),
    ],
)
async def test_invalid_content_is_rejected_and_leaves_no_upload(client, registry, name, data):
    response = await upload(client, name, data)
    assert response.status_code == 422, response.text
    assert store.list_uploads(registry.config.store_path, "minh") == []
    assert list(registry.root.rglob("*.csv")) == []


@pytest.mark.asyncio
async def test_real_workbook_disguised_as_csv_is_rejected(client, registry):
    assert (await upload(client, "fake.csv", workbook_bytes())).status_code == 422


@pytest.mark.asyncio
async def test_xlsx_preserves_empty_cells_and_ignores_trailing_header_formatting(client, registry):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["id", "name"])
    sheet.cell(1, 12).number_format = "0.00"
    sheet.append([1, "Lan"])
    sheet.append([2])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    response = await upload(client, "blank-cells.xlsx", output.getvalue())
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert metadata["tables"][0]["columns"] == ["id", "name"]
    assert metadata["tables"][0]["row_count"] == 2
    path = registry.root / metadata["root"] / metadata["tables"][0]["name"] / "data.csv"
    assert path.read_text(encoding="utf-8").splitlines() == ["id,name", "1,Lan", "2,"]


@pytest.mark.asyncio
async def test_upload_api_requires_a_session_before_reading_body(client, registry):
    app.dependency_overrides.pop(auth.current_user)
    assert (await upload(client)).status_code == 401
    assert (await client.get("/api/v1/uploads")).status_code == 401
    assert (await client.delete("/api/v1/uploads/missing")).status_code == 401
    assert not registry.root.exists()


@pytest.mark.asyncio
async def test_file_and_total_budgets_are_read_from_policy(client, registry, monkeypatch):
    limits(monkeypatch, file_bytes=len(CSV) - 1, total_bytes=100)
    assert (await upload(client)).status_code == 413
    assert store.list_uploads(registry.config.store_path, "minh") == []
    limits(monkeypatch, file_bytes=100, total_bytes=2 * len(CSV))
    first = (await upload(client)).json()
    response = await upload(client)
    # CSV chuẩn hóa thêm CRLF, quota tính dung lượng lớn hơn giữa input và CSV đã lưu.
    assert response.status_code == 413, response.text
    assert len(store.list_uploads(registry.config.store_path, "minh")) == 1
    assert len(list(registry.root.rglob("*.csv"))) == 1
    assert (await client.delete(f"/api/v1/uploads/{first['id']}")).status_code == 204
    assert (await upload(client)).status_code == 201
    limits(monkeypatch, file_bytes=100, total_bytes=len(CSV))
    assert (
        await client.post("/api/v1/uploads", files=[("files", ("a.csv", CSV)), ("files", ("b.csv", CSV))])
    ).status_code == 413


@pytest.mark.asyncio
async def test_converted_xlsx_size_and_zip_expansion_are_bounded(client, registry, monkeypatch):
    data = workbook_bytes()
    limits(monkeypatch, file_bytes=len(data) + 1, total_bytes=len(data) + 1)
    response = await upload(client, "sheets.xlsx", data)
    assert response.status_code == 413 and "giải nén" in response.json()["detail"]
    limits(monkeypatch, file_bytes=len(CSV), total_bytes=10000)
    response = await upload(client)
    assert response.status_code == 413 and "chuyển đổi" in response.json()["detail"]


@pytest.mark.asyncio
async def test_chunked_oversized_multipart_is_stopped_before_spooling_forever(client, registry, monkeypatch):
    limits(monkeypatch, file_bytes=10, total_bytes=10)

    async def body():
        yield b'--boundary\r\nContent-Disposition: form-data; name="files"; filename="large.csv"\r\n\r\n'
        yield b"x" * (1024**2 + 20)
        yield b"\r\n--boundary--\r\n"

    response = await client.post(
        "/api/v1/uploads", content=body(), headers={"Content-Type": "multipart/form-data; boundary=boundary"}
    )
    assert response.status_code == 413
    assert store.list_uploads(registry.config.store_path, "minh") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../escape.csv", "..\\escape.csv", "C:\\temp\\escape.csv", "/tmp/escape.csv"])
async def test_filename_paths_never_leave_upload_directory(client, registry, name):
    response = await upload(client, name)
    assert response.status_code == 201, response.text
    metadata = response.json()
    directory = registry.root / metadata["root"]
    assert list(registry.root.rglob("*.csv")) == [directory / "escape" / "data.csv"]
    assert metadata["files"] == ["escape.csv"]


@pytest.mark.asyncio
async def test_sheet_title_paths_are_normalized_without_zip_extraction(client, registry):
    original = workbook_bytes()
    output = io.BytesIO()
    with ZipFile(io.BytesIO(original)) as source, ZipFile(output, "w") as target:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == "xl/workbook.xml":
                data = data.replace('name="Đơn hàng"'.encode(), b'name="../escape"')
            target.writestr(entry, data)
    response = await upload(client, "traversal.xlsx", output.getvalue())
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert metadata["tables"][0]["name"] == "escape"
    assert all(path.is_relative_to(registry.root / metadata["root"]) for path in registry.root.rglob("*.csv"))


@pytest.mark.asyncio
async def test_owner_only_list_selection_delete_and_linked_reviewer_domain_visibility(client, registry):
    metadata = (await upload(client)).json()
    selected = (await client.put(f"/api/v1/uploads/{metadata['id']}/selection", json={"tables": ["orders"]})).json()
    registry.user = {"id": "engineer_other", "role": "Engineer"}
    assert (await client.get("/api/v1/uploads")).json() == []
    assert (await client.delete(f"/api/v1/uploads/{metadata['id']}")).status_code == 404
    assert (
        await client.put(f"/api/v1/uploads/{metadata['id']}/selection", json={"tables": ["orders"]})
    ).status_code == 404
    assert selected["domain"] not in effective_policy(registry.config, owner_id="engineer_other").domains.domains
    assert (await client.get(f"/api/v1/catalog/sources/{selected['source_ref']}/objects")).status_code == 404
    assert selected["domain"] not in policy_for({"id": "outsider", "role": "Reviewer"}, registry.config).domains.domains
    with store.database(registry.config.store_path) as conn:
        conn.execute("INSERT INTO reviewer_links VALUES ('minh', 'linked', 1)")
    registry.user = {"id": "linked", "role": "Reviewer"}
    assert selected["domain"] in policy_for(registry.user, registry.config).domains.domains
    assert (await client.get(f"/api/v1/catalog/sources/{selected['source_ref']}/objects")).status_code == 200
    assert (await upload(client)).status_code == 403
    assert (await client.delete(f"/api/v1/uploads/{metadata['id']}")).status_code == 403
    registry.user = {"id": "minh", "role": "Engineer"}
    assert (await client.delete(f"/api/v1/uploads/{metadata['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/uploads/{metadata['id']}")).status_code == 404
    assert selected["domain"] not in effective_policy(registry.config).domains.domains
    assert not (registry.root / metadata["root"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tables", [[], ["missing"], ["orders", "orders"], ["../orders"]])
async def test_selection_is_validated_against_detected_tables(client, registry, tables):
    metadata = (await upload(client)).json()
    response = await client.put(f"/api/v1/uploads/{metadata['id']}/selection", json={"tables": tables})
    assert response.status_code == 422
    assert store.get_upload(registry.config.store_path, metadata["id"], "minh")["selected_tables"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,deployed_at,expected",
    [("DEPLOYED", None, 409), ("APPROVED", "2026-01-01T00:00:00Z", 409), ("APPROVED", None, 204)],
)
@pytest.mark.parametrize("reference", ["connection_ref", "source_ref"])
async def test_delete_blocks_any_recorded_deployment(client, registry, status, deployed_at, expected, reference):
    metadata = (await upload(client)).json()
    report = json.loads(Path("fixtures/run_reports/deployed.json").read_text(encoding="utf-8"))
    report["status"] = status
    value = f"upload:{metadata['id']}" if reference == "connection_ref" else f"f_{metadata['id'][:8]}"
    report["spec"]["sources"] = [{reference: value}]
    if deployed_at:
        report["deployment"] = {
            "repo": "owner/pipelines",
            "branch": "pipeline/upload",
            "pr_number": 1,
            "pr_url": "https://example.com/pr/1",
            "head_sha": "abc",
            "opened_at": deployed_at,
            "deployed_at": deployed_at,
        }
    save_run_report(registry.config.store_path, RunReport.model_validate(report))
    response = await client.delete(f"/api/v1/uploads/{metadata['id']}")
    assert response.status_code == expected, response.text
    assert (registry.root / metadata["root"]).exists() == (expected == 409)


def test_store_total_quota_is_atomic_for_concurrent_uploads(registry):
    def save(index):
        try:
            store.save_upload(registry.config.store_path, {"id": str(index), "owner_id": "minh", "size_bytes": 60}, 100)
            return True
        except store.UploadQuotaError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, [1, 2])) == [False, True]


@pytest.mark.asyncio
async def test_failed_disk_delete_keeps_metadata_for_retry(client, registry, monkeypatch):
    metadata = (await upload(client)).json()
    monkeypatch.setattr(uploads.shutil, "rmtree", lambda directory: (_ for _ in ()).throw(OSError("disk")))
    assert (await client.delete(f"/api/v1/uploads/{metadata['id']}")).status_code == 503
    assert store.get_upload(registry.config.store_path, metadata["id"], "minh") is not None


@pytest.mark.asyncio
async def test_upload_decl_supplies_sandbox_file_url_and_dlt_reads_all_varchar(client, registry, tmp_path):
    import dlt
    import duckdb
    from dlt.sources.filesystem import filesystem, read_csv_duckdb

    from src.codegen.sandbox import _source_env

    metadata = (await upload(client)).json()
    selected = (await client.put(f"/api/v1/uploads/{metadata['id']}/selection", json={"tables": ["orders"]})).json()
    decl = (
        effective_policy(registry.config, owner_id="minh")
        .domains.domains[selected["domain"]]
        .sources[selected["source_ref"]]
    )
    env = _source_env(selected["source_ref"], decl, registry.config)
    url = env[f"SOURCES__{selected['source_ref'].upper()}__BUCKET_URL"]
    assert url == (registry.root / metadata["root"]).as_uri()
    resource = (filesystem(bucket_url=url, file_glob="orders/*.csv") | read_csv_duckdb(all_varchar=True)).with_name(
        "orders"
    )
    warehouse = str(tmp_path / "upload.duckdb")
    pipeline = dlt.pipeline(
        pipeline_name="upload_test",
        pipelines_dir=str(tmp_path / "dlt"),
        destination=dlt.destinations.duckdb(warehouse),
        dataset_name="bronze",
    )
    pipeline.run(resource, write_disposition="replace", loader_file_format="parquet")
    with duckdb.connect(warehouse) as db:
        assert db.execute("SELECT id, amount FROM bronze.orders ORDER BY id").fetchall() == [("1", "120"), ("2", "340")]
        assert (
            db.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_name='orders' AND column_name='amount'"
            ).fetchone()[0]
            == "VARCHAR"
        )
