import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.codegen.render import file_hashes
from src.deployer.manifest import (
    ManifestError,
    build_manifest,
    hash_files,
    load_json,
    verify_manifest,
    verify_report,
)
from src.run_report import RunReport

FILES = {
    "ingestion/fct_x_pipeline.py": "print('el')\n",
    "dbt_project/models/gold/fct_x.sql": "select 1 as n\n",
    "dbt_project/models/gold/fct_x.yml": "version: 2\n",
    "infra/dagster/generated/fct_x_asset.py": "# asset\n",
}


def manifest(files=FILES, **changes) -> dict:
    args = {
        "run_id": "r1",
        "version": 2,
        "model_name": "fct_x",
        "files": files,
        "reviewer": "an",
        "approved_at": datetime(2026, 10, 10, tzinfo=UTC),
    }
    return build_manifest(**{**args, **changes})


def test_hashes_match_the_approval_signature_algorithm():
    assert hash_files(FILES) == file_hashes(FILES)


def test_manifest_records_signature_and_every_file():
    built = manifest()

    assert built == {
        "manifest_version": 1,
        "run_id": "r1",
        "version": 2,
        "model_name": "fct_x",
        "files_hash": file_hashes(FILES)[1],
        "files": file_hashes(FILES)[0],
        "reviewer": "an",
        "approved_at": "2026-10-10T00:00:00+00:00",
    }
    verify_manifest(built, FILES)


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({k: v for k, v in FILES.items() if "yml" not in k}, "thiếu"),
        ({**FILES, "extra.sql": "select 2\n"}, "thừa"),
        ({**FILES, "dbt_project/models/gold/fct_x.sql": "select 2 as n\n"}, "sha256"),
    ],
)
def test_verify_rejects_files_that_differ_from_manifest(files, message):
    with pytest.raises(ManifestError, match=message):
        verify_manifest(manifest(), files)


def test_verify_rejects_forged_files_hash_and_incomplete_manifest():
    with pytest.raises(ManifestError, match="files_hash"):
        verify_manifest({**manifest(), "files_hash": "sha256:khac"}, FILES)
    with pytest.raises(ManifestError, match="reviewer"):
        verify_manifest({**manifest(), "reviewer": ""}, FILES)
    with pytest.raises(ManifestError, match="manifest_version"):
        verify_manifest({**manifest(), "manifest_version": 99}, FILES)


UNSAFE_PATHS = [
    "../escape.sql",
    "a/../../escape.sql",
    "/abs.sql",
    "a\\b.sql",
    "..\\escape.sql",
    "C:/outside.sql",
    "c:x.sql",
    "C:\\outside.sql",
    "//server/share/x.sql",
    "manifest.json",
    "run_report.json",
    "",
    # Bí danh: cùng một file trên đĩa với tên khác dạng chuẩn.
    "./manifest.json",
    "./run_report.json",
    "a/./b.sql",
    "./a.sql",
    "a//b.sql",
    "a/",
    ".",
    "Manifest.JSON",
    "RUN_REPORT.json",
]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(ManifestError, match="Đường dẫn"):
        manifest(files={**FILES, path: "x"})


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_verify_rejects_manifest_listing_unsafe_path(path):
    # Manifest giả mạo trên nhánh: kể cả khi bộ file khớp từng sha256, đường dẫn ra ngoài thư mục vẫn bị từ chối.
    files = {**FILES, path: "x"}
    forged = {**manifest(), "files": hash_files(files)[0], "files_hash": hash_files(files)[1]}

    with pytest.raises(ManifestError, match="Đường dẫn"):
        verify_manifest(forged, files)


@pytest.mark.parametrize("model_name", ["../x", "Fct", "fct/x", ""])
def test_model_name_must_be_an_identifier(model_name):
    with pytest.raises(ManifestError, match="Tên model"):
        manifest(model_name=model_name)


def test_nested_file_named_like_reserved_file_is_allowed():
    files = {**FILES, "dbt_project/manifest.json": "{}\n"}

    verify_manifest(manifest(files=files), files)


@pytest.mark.parametrize(
    "broken",
    [
        [],
        "manifest",
        {**manifest(), "files": ["dbt_project/models/gold/fct_x.sql"]},
        {**manifest(), "files": {**manifest()["files"], "ingestion/fct_x_pipeline.py": 1}},
        {**manifest(), "files": {1: "sha256:x"}},
        {**manifest(), "version": True},
        {**manifest(), "version": "2"},
        {**manifest(), "manifest_version": True},
        {**manifest(), "run_id": 5},
        {**manifest(), "model_name": None},
    ],
)
def test_verify_rejects_wrong_types_without_crashing(broken):
    with pytest.raises(ManifestError):
        verify_manifest(broken, FILES)


def test_verify_rejects_file_contents_that_are_not_text():
    with pytest.raises(ManifestError):
        verify_manifest(manifest(), {**FILES, "ingestion/fct_x_pipeline.py": b"print('el')\n"})


@pytest.mark.parametrize("text", ["{", "[1, 2]", '"chuoi"', "null", pytest.param("[" * 100000, id="long-nested")])
def test_load_json_rejects_broken_or_non_object_json(text):
    with pytest.raises(ManifestError, match="manifest.json"):
        load_json(text, "manifest.json")


def signed_report(built: dict) -> dict:
    """run_report.json như backend ghi lúc mở pull request (RunReport.model_dump_json)."""
    approval = {
        "decision": "approved",
        "reviewer": built["reviewer"],
        "files_hash": built["files_hash"],
        "version": built["version"],
        "at": "2026-10-10T00:00:00Z",
        "comment": "",
    }
    return {
        "run_id": built["run_id"],
        "version": built["version"],
        "files_hash": built["files_hash"],
        "status": "APPROVED",
        "spec": {"transform": {"model_name": built["model_name"]}},
        "approval": approval,
    }


def test_report_signed_by_the_manifest_passes():
    built = manifest()

    verify_report(built, signed_report(built))


def test_real_run_report_serialization_matches_manifest():
    report = RunReport.model_validate_json(Path("fixtures/run_reports/deployed.json").read_text(encoding="utf-8"))
    built = {
        "run_id": report.run_id,
        "version": report.version,
        "model_name": report.spec["transform"]["model_name"],
        "files_hash": report.files_hash,
        "reviewer": report.approval.reviewer,
        "approved_at": report.approval.at.isoformat(),
    }

    verify_report(built, json.loads(report.model_dump_json(indent=2, exclude={"deployment"})))


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(run_id="r2"),
        lambda r: r.update(version=3),
        lambda r: r.update(files_hash="sha256:khac"),
        lambda r: r["approval"].update(files_hash="sha256:khac"),
        lambda r: r["approval"].update(reviewer="ke_gia"),
        lambda r: r["approval"].update(decision="rejected"),
        lambda r: r["approval"].update(version=True),
        lambda r: r["approval"].update(at="2026-10-11T00:00:00Z"),
        lambda r: r["approval"].update(at="khong-phai-ngay"),
        lambda r: r.update(approval=None),
        lambda r: r.update(spec={"transform": {"model_name": "fct_khac"}}),
        lambda r: r.update(spec="fct_x"),
    ],
)
def test_report_that_does_not_carry_the_signature_is_rejected(change):
    built = manifest()
    report = signed_report(built)
    change(report)

    with pytest.raises(ManifestError, match="run_report.json"):
        verify_report(built, report)


def test_report_must_be_an_object():
    with pytest.raises(ManifestError, match="run_report.json"):
        verify_report(manifest(), ["không phải object"])
