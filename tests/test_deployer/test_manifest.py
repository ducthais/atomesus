from datetime import UTC, datetime

import pytest

from src.codegen.render import file_hashes
from src.deployer.manifest import ManifestError, build_manifest, hash_files, verify_manifest

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


@pytest.mark.parametrize("path", ["../escape.sql", "/abs.sql", "a\\b.sql", "manifest.json", "run_report.json", ""])
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(ManifestError, match="Đường dẫn"):
        manifest(files={**FILES, path: "x"})


@pytest.mark.parametrize("model_name", ["../x", "Fct", "fct/x", ""])
def test_model_name_must_be_an_identifier(model_name):
    with pytest.raises(ManifestError, match="Tên model"):
        manifest(model_name=model_name)
