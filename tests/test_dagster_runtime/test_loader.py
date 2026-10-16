"""Code location hợp nhất (lỗi #4): nạp mọi bundle v2, bỏ qua bundle cũ / hỏng / bản tạm mà không làm sập location."""

import importlib
import os
import sys

import pytest

pytest.importorskip("dagster_dbt")

from src.dagster_runtime import DEPLOY_DIR_ENV  # noqa: E402
from src.monitor.runs import deployed_models  # noqa: E402
from tests.test_dagster_runtime.specs import (  # noqa: E402
    RENTAL_MODEL,
    REVIEW_MODEL,
    install_parsed,
    rental_spec,
    review_spec,
)


def _legacy(deploy, model="fct_old"):
    folder = deploy / model / "infra" / "dagster" / "generated"
    folder.mkdir(parents=True)
    # Bundle template cũ: đọc credential ngay lúc import và ghi đè env của tiến trình. Không được chạy dòng nào.
    (folder / f"{model}_asset.py").write_text(
        "import os\nos.environ['WAREHOUSE_PATH'] = 'shared.duckdb'\nraise RuntimeError('không được import')\n",
        encoding="utf-8",
    )


@pytest.fixture
def deploy(tmp_path, monkeypatch):
    for key in [k for k in os.environ if k.startswith("SOURCES__") or k == "WAREHOUSE_PATH"]:
        monkeypatch.delenv(key)
    root = tmp_path / "deploy"
    install_parsed(root, review_spec())
    install_parsed(root, rental_spec())
    _legacy(root)
    broken = root / "fct_broken" / "infra" / "dagster" / "generated"
    broken.mkdir(parents=True)
    (broken / "fct_broken_asset.py").write_text("RUNTIME_CONTRACT = 2\nraise ValueError('hỏng')\n", encoding="utf-8")
    (root / f"{RENTAL_MODEL}.tmp-abc").mkdir()
    (root / f"{RENTAL_MODEL}.old-abc").mkdir()
    (root / ".generation").write_text("1.a", encoding="utf-8")
    yield root
    for name in [m for m in sys.modules if m.startswith("atomesus_pipeline_")]:
        sys.modules.pop(name)


def test_loader_merges_current_bundles_and_skips_the_rest(deploy):
    from src.dagster_runtime.loader import load_definitions

    before = dict(os.environ)

    defs, skipped = load_definitions(deploy)

    jobs = {job.name for job in defs.get_repository_def().get_all_jobs() if not job.name.startswith("__")}
    assert jobs == {f"{REVIEW_MODEL}_job", f"{RENTAL_MODEL}_job"}
    assert set(skipped) == {"fct_old", "fct_broken"} and "template cũ" in skipped["fct_old"]
    assert skipped["fct_broken"] == "nạp lỗi (ValueError)"
    assert "atomesus_pipeline_fct_old" not in sys.modules and "atomesus_pipeline_fct_broken" not in sys.modules
    assert dict(os.environ) == before, "bundle cũ không được chạy và không đổi env"


def test_module_level_defs_read_the_deploy_dir_from_env(deploy, monkeypatch):
    monkeypatch.setenv(DEPLOY_DIR_ENV, str(deploy))
    sys.modules.pop("src.dagster_runtime.loader", None)

    loader = importlib.import_module("src.dagster_runtime.loader")

    assert {s.name for s in loader.defs.get_repository_def().schedule_defs} == {
        f"{REVIEW_MODEL}_job_schedule",
        f"{RENTAL_MODEL}_job_schedule",
    }


def test_empty_or_missing_deploy_dir_gives_an_empty_location(tmp_path):
    from src.dagster_runtime.loader import load_definitions

    defs, skipped = load_definitions(tmp_path / "missing")

    assert skipped == {} and list(defs.get_repository_def().get_all_jobs()) == []


def test_day2_ignores_legacy_and_temporary_bundles(deploy):
    (deploy / "fct_no_asset").mkdir()  # thư mục pipeline không có file asset (dữ liệu cũ của Day 2) vẫn được xét

    assert deployed_models(str(deploy)) == ["fct_broken", "fct_no_asset", RENTAL_MODEL, REVIEW_MODEL]
