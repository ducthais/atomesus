import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.deployer import install
from src.deployer.manifest import ManifestError
from src.deployer.verify import VerificationError
from src.monitor import detectors, runs
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy

ROOT = Path(__file__).resolve().parents[2]


def fake_project(root, files):
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_install_replaces_project_and_marks_a_new_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    old = tmp_path / "fct_x"
    old.mkdir()
    (old / "stale.txt").write_text("old", encoding="utf-8")

    target = install.install_project("fct_x", {"infra/dagster/generated/fct_x_asset.py": "# asset\n"}, str(tmp_path))

    assert target == old and not (target / "stale.txt").exists()
    assert (target / "infra/dagster/generated/fct_x_asset.py").read_text(encoding="utf-8") == "# asset\n"
    assert (tmp_path / ".generation").read_text(encoding="utf-8").strip()
    assert sorted(p.name for p in tmp_path.iterdir()) == [".generation", "fct_x"], "không còn thư mục tạm/backup"


def test_each_install_writes_a_different_generation(tmp_path, monkeypatch):
    # Supervisor Dagster so nội dung `.generation`: mỗi lần cài phải khác để code location được nạp lại.
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    install.install_project("fct_x", {"infra/dagster/generated/fct_x_asset.py": "# asset\n"}, str(tmp_path))
    first = (tmp_path / ".generation").read_text(encoding="utf-8")

    install.install_project("fct_x", {"infra/dagster/generated/fct_x_asset.py": "# asset v2\n"}, str(tmp_path))

    assert (tmp_path / ".generation").read_text(encoding="utf-8") != first
    assert sorted(p.name for p in tmp_path.iterdir()) == [".generation", "fct_x"]


def test_generation_is_written_atomically_without_leftovers(tmp_path):
    path = install.write_generation(str(tmp_path / "deploy"))
    again = install.write_generation(str(tmp_path / "deploy"))

    assert path == again and path.name == ".generation" and path.read_text(encoding="utf-8").count(".") == 2
    assert [p.name for p in path.parent.iterdir()] == [".generation"]


def test_failed_generation_write_restores_previous_project(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))
    old = tmp_path / "fct_x"
    old.mkdir()
    (old / "marker.txt").write_text("old", encoding="utf-8")

    def broken(_deploy_dir):
        raise OSError("đĩa đầy")

    monkeypatch.setattr(install, "write_generation", broken)

    with pytest.raises(OSError, match="đĩa đầy"):
        install.install_project("fct_x", {"a.sql": "select 1\n"}, str(tmp_path))

    assert (old / "marker.txt").read_text(encoding="utf-8") == "old" and not (old / "a.sql").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fct_x"]


def test_model_name_cannot_escape_deploy_dir(tmp_path):
    with pytest.raises(ManifestError):
        install.install_project("../evil", {"a.sql": "select 1\n"}, str(tmp_path / "deploy"))

    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize("first_install", [False, True])
def test_rollback_marks_a_new_generation_even_when_new_directory_cannot_be_deleted(
    tmp_path, monkeypatch, first_install
):
    monkeypatch.setattr(install, "prepare_project", fake_project)
    monkeypatch.setattr(install, "_compile_manifest", lambda _root: None)
    asset = "infra/dagster/generated/fct_x_asset.py"
    if not first_install:
        install.install_project("fct_x", {asset: "# old\n"}, str(tmp_path))
    before = install.write_generation(str(tmp_path)).read_bytes()
    original = VerificationError("Chạy thử Dagster kết thúc FAILURE (run failed-run).")
    remove = install.shutil.rmtree

    def broken_remove(target, **kwargs):
        if target == tmp_path / "fct_x" or ".tmp-" in target.name:
            if kwargs.get("ignore_errors"):
                return
            raise PermissionError("secret-path")
        return remove(target, **kwargs)

    def fail(_target):
        raise original

    monkeypatch.setattr(install.shutil, "rmtree", broken_remove)

    with pytest.raises(VerificationError, match="FAILURE.*Không xóa được thư mục mới") as error:
        install.install_project("fct_x", {asset: "# new\n"}, str(tmp_path), verify=fail)

    assert error.value.__cause__ is original and "secret-path" not in str(error.value)
    assert (tmp_path / ".generation").read_bytes() != before, "rollback ghi mốc mới để Dagster nạp lại bản cũ"
    if first_install:
        assert not (tmp_path / "fct_x").exists()
    else:
        assert (tmp_path / "fct_x" / asset).read_text(encoding="utf-8") == "# old\n"
    [quarantined] = list(tmp_path.glob("fct_x.tmp-*"))
    assert (quarantined / asset).read_text(encoding="utf-8") == "# new\n"
    assert not list(tmp_path.glob("*.old-*"))


@pytest.mark.parametrize("timed_out", [False, True])
def test_dbt_parse_errors_include_sanitized_tails_of_both_streams(tmp_path, monkeypatch, timed_out):
    monkeypatch.setenv("PIPELINE_DEPLOYER_SECRET", "known-secret")
    stdout = "X" * 3000 + "\nCompilation Error: env=known-secret password='literal password'"
    stderr = "Y" * 3000 + "\nMissing macro; https://user:pass@example.com/?token=literal token=another-secret"

    def parse(command, **kwargs):
        if timed_out:
            raise subprocess.TimeoutExpired(command, 120, stdout.encode(), stderr.encode())
        return subprocess.CompletedProcess(command, 2, stdout, stderr)

    monkeypatch.setattr(install.subprocess, "run", parse)

    with pytest.raises(subprocess.TimeoutExpired if timed_out else OSError, match="dbt parse") as error:
        install._compile_manifest(tmp_path)

    detail = str(error.value)
    assert "stdout:" in detail and "stderr:" in detail
    assert "Compilation Error" in detail and "Missing macro" in detail
    assert len(detail) < 400
    for secret in (
        "known-secret",
        "literal password",
        "user:pass",
        "another-secret",
        "example.com",
        "X" * 200,
        "Y" * 200,
    ):
        assert secret not in detail


def test_parse_detail_redacts_before_truncation_and_masks_authorization(monkeypatch):
    from urllib.parse import quote

    secret = "long secret/" * 30
    monkeypatch.setenv("PIPELINE_GITHUB_TOKEN", secret)
    detail = install._parse_detail(
        "\x1b[31mCompilation Error\x1b[0m " + quote(secret, safe=""),
        'Authorization: Bearer opaque-value; api_key="literal key" password: "literal password"',
    )

    assert "Compilation Error" in detail and "\x1b" not in detail
    for value in ("long", "opaque-value", "literal key", "literal password"):
        assert value not in detail
    # Dựng marker PEM giả lúc chạy để bộ quét secret không nhận fixture là khóa thật.
    marker = "-" * 5 + "BEGIN " + "PRIVATE KEY" + "-" * 5
    private = install._parse_detail(marker + "\nfixture không phải khóa", None)
    assert private == "stdout: ***"


def test_install_runs_real_dbt_parse_without_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WAREHOUSE_PATH", raising=False)
    # Không được ghi manifest sang thư mục artifact của Dagster do biến môi trường thừa kế.
    monkeypatch.setenv("DBT_TARGET_PATH", str(tmp_path / "unwanted-target"))
    assert not (tmp_path / ".env").exists()

    project = install.install_project(
        "fct_x",
        {
            "dbt_project/models/gold/fct_x.sql": "select 1 as n\n",
            "infra/dagster/generated/fct_x_asset.py": "# asset\n",
        },
        str(tmp_path / "deploy"),
    )

    manifest = json.loads((project / "dbt_project/target/manifest.json").read_text(encoding="utf-8"))
    assert any(node["name"] == "fct_x" for node in manifest["nodes"].values())
    assert not (tmp_path / "unwanted-target").exists()
    assert (project.parent / ".generation").is_file()


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
    (deploy / ".generation").write_text("1.a\n", encoding="utf-8")
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
    assert sorted(p.name for p in deploy.iterdir()) == [".generation", model], "không ghi gì vào code location"


def test_day2_bridge_matches_real_dagster_dbt_artifact_names(tmp_path, monkeypatch):
    from dagster_dbt import DbtCliResource

    model, home = "fct_x", tmp_path / "home"
    deploy = tmp_path / "code"
    (deploy / model).mkdir(parents=True)
    monkeypatch.setenv("DBT_TARGET_PATH", str(home / "dbt_target"))
    resource = DbtCliResource(project_dir=ROOT / "dbt_project")
    context = SimpleNamespace(
        op_execution_context=SimpleNamespace(op=SimpleNamespace(name=f"{model}_transform")),
        run=SimpleNamespace(run_id="abcdef012345"),
    )
    target = resource._get_unique_target_path(context=context)
    at = datetime(2026, 10, 10, tzinfo=UTC).timestamp()
    _dbt_results(home / "dbt_target", target.name, at, failed=1)
    settings = PipelineSettings(_env_file=None, deploy_dir=str(deploy))

    view = _dagster_local().day2_settings(settings, home=home)

    assert target.parent == home / "dbt_target" and target.name.startswith(f"{model}_transform-abcdef0-")
    assert runs.dbt_results(Path(view.deploy_dir) / model, at, at + 60) == {"tests": 4, "tests_failed": 1}
