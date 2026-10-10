import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.deployer import install
from src.deployer.manifest import ManifestError
from src.monitor import detectors, runs
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy

ROOT = Path(__file__).resolve().parents[2]


def fake_project(root, files):
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_install_replaces_project_and_lists_it_in_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    old = tmp_path / "fct_x"
    old.mkdir()
    (old / "stale.txt").write_text("old", encoding="utf-8")

    target = install.install_project("fct_x", {"infra/dagster/generated/fct_x_asset.py": "# asset\n"}, str(tmp_path))

    assert target == old and not (target / "stale.txt").exists()
    assert (target / "infra/dagster/generated/fct_x_asset.py").read_text(encoding="utf-8") == "# asset\n"
    workspace = (tmp_path / "workspace.yaml").read_text(encoding="utf-8")
    assert "module_name: infra.dagster.generated.fct_x_asset" in workspace and "location_name: fct_x" in workspace
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fct_x", "workspace.yaml"], "không còn thư mục tạm/backup"


def test_reinstall_lists_only_live_project_not_its_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    asset = {"infra/dagster/generated/fct_x_asset.py": "# asset\n"}
    install.install_project("fct_x", asset, str(tmp_path))

    install.install_project("fct_x", {"infra/dagster/generated/fct_x_asset.py": "# asset v2\n"}, str(tmp_path))

    workspace = (tmp_path / "workspace.yaml").read_text(encoding="utf-8")
    directories = [line.split(": ", 1)[1] for line in workspace.splitlines() if "working_directory:" in line]
    assert directories == ["./fct_x"] and workspace.count("location_name:") == 1
    assert (tmp_path / directories[0]).is_dir()


def test_empty_workspace_is_valid_yaml(tmp_path):
    path = install.write_workspace(str(tmp_path / "deploy"))

    assert path.read_text(encoding="utf-8").splitlines()[-1] == "load_from: []"


def test_failed_workspace_write_restores_previous_project(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    old = tmp_path / "fct_x"
    old.mkdir()
    (old / "marker.txt").write_text("old", encoding="utf-8")

    def broken(_deploy_dir):
        raise OSError("đĩa đầy")

    monkeypatch.setattr(install, "write_workspace", broken)

    with pytest.raises(OSError, match="đĩa đầy"):
        install.install_project("fct_x", {"a.sql": "select 1\n"}, str(tmp_path))

    assert (old / "marker.txt").read_text(encoding="utf-8") == "old" and not (old / "a.sql").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fct_x"]


def test_model_name_cannot_escape_deploy_dir(tmp_path):
    with pytest.raises(ManifestError):
        install.install_project("../evil", {"a.sql": "select 1\n"}, str(tmp_path / "deploy"))

    assert not (tmp_path / "evil").exists()


def _dagster_local():
    spec = importlib.util.spec_from_file_location("dagster_local_under_test", ROOT / "scripts" / "dagster_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dbt_results(artifacts, folder, at, failed):
    path = artifacts / folder / "run_results.json"
    path.parent.mkdir(parents=True)
    tests = [{"unique_id": f"test.x.t{i}", "status": "fail" if i < failed else "pass"} for i in range(4)]
    path.write_text(json.dumps({"results": [{"unique_id": "model.x.m", "status": "success"}, *tests]}), "utf-8")
    os.utime(path, (at + 30, at + 30))


def test_production_dbt_results_reach_day2_test_degradation(tmp_path):
    """Production: code location chỉ đọc, dbt ghi run_results.json vào DAGSTER_HOME/dbt_target; Day 2 vẫn đếm được
    test và luật test_degradation vẫn kích hoạt."""
    model, t0 = "fct_x", datetime(2026, 10, 6, 2, 0, tzinfo=UTC).timestamp()
    deploy, home = tmp_path / "dagster_local", tmp_path / "dagster_home"
    (deploy / model / "dbt_project").mkdir(parents=True)  # code location đã cài, không có target/
    (deploy / "workspace.yaml").write_text("load_from: []\n", encoding="utf-8")
    artifacts = home / "dbt_target"
    _dbt_results(artifacts, f"{model}_transform-r1aaaaa-0000001", t0, failed=0)
    _dbt_results(artifacts, f"{model}_transform-r2bbbbb-0000002", t0 + 3600, failed=2)
    _dbt_results(artifacts, "fct_y_transform-r9ccccc-0000003", t0 + 3600, failed=4)  # pipeline khác, cùng lúc
    settings = PipelineSettings(
        _env_file=None,
        store_path=str(tmp_path / "runs.sqlite3"),
        deploy_dir=str(deploy),
        warehouse_path=str(tmp_path / "warehouse.duckdb"),
    )
    found = [
        {
            "runId": f"r{i}" + "a" * 6,
            "jobName": f"{model}_job",
            "status": "SUCCESS",
            "startTime": at,
            "endTime": at + 60,
        }
        for i, at in ((1, t0), (2, t0 + 3600))
    ]

    def collect(target):
        return runs.collect(target, load_policy(), runs=lambda url: found, estimate=lambda *a: {}, counts=lambda *a: {})

    assert [r["tests"] for r in collect(settings.model_copy(update={"store_path": str(tmp_path / "raw.db")}))] == [
        None,
        None,
    ], "không gom lại thì Day 2 không thấy kết quả dbt của production"

    view = _dagster_local().day2_settings(settings, home=home)
    written = collect(view)

    assert [(r["tests"], r["tests_failed"]) for r in written] == [(4, 0), (4, 2)]
    series = runs.series(settings.store_path, model)
    now = datetime.fromtimestamp(t0 + 3700, UTC)
    found_rules = [f.rule for f in detectors.detect(series, load_policy().objectives["sales"], now)]
    assert "test_degradation" in found_rules
    assert sorted(p.name for p in (home / "day2_dbt").iterdir()) == [model], "chỉ pipeline đang cài"
    assert sorted(p.name for p in deploy.iterdir()) == [model, "workspace.yaml"], "không ghi gì vào code location"
