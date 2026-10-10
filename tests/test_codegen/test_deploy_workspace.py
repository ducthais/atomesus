import subprocess
from pathlib import Path

import pytest

from src.codegen import deploy
from src.spec.models import Spec
from tests.test_agents.test_graph import SPEC


def _make_project(root: Path, model: str, marker: str) -> None:
    generated = root / "infra" / "dagster" / "generated"
    generated.mkdir(parents=True)
    (generated / f"{model}_asset.py").write_text(f"# {marker}\n", encoding="utf-8")
    (root / "marker.txt").write_text(marker, encoding="utf-8")


def _locations(deploy_dir: Path) -> list[str]:
    text = (deploy_dir / "workspace.yaml").read_text(encoding="utf-8")
    return [line.split(": ", 1)[1] for line in text.splitlines() if "working_directory:" in line]


def test_write_workspace_skips_staging_backup_and_hidden_dirs(tmp_path):
    _make_project(tmp_path / "sales", "sales", "real")
    _make_project(tmp_path / "sales.old-abc", "sales", "backup")
    _make_project(tmp_path / "sales.tmp-abc", "sales", "staging")
    _make_project(tmp_path / ".hidden", "sales", "hidden")

    deploy.write_workspace(str(tmp_path))

    assert _locations(tmp_path) == ["./sales"]


def test_write_workspace_with_only_backup_dirs_is_empty(tmp_path):
    _make_project(tmp_path / "sales.old-abc", "sales", "backup")

    deploy.write_workspace(str(tmp_path))

    assert "load_from: []" in (tmp_path / "workspace.yaml").read_text(encoding="utf-8")


def test_redeploy_existing_pipeline_points_workspace_only_at_live_project(tmp_path, monkeypatch):
    spec = Spec.model_validate(SPEC)
    model = spec.transform.model_name
    _make_project(tmp_path / model, model, "old")
    deploy.write_workspace(str(tmp_path))
    monkeypatch.setattr(deploy, "prepare_project", lambda root, _files: _make_project(root, model, "new"))
    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))

    deploy.deploy_local(spec, {}, str(tmp_path))

    locations = _locations(tmp_path)
    assert locations == [f"./{model}"]
    assert (tmp_path / locations[0]).is_dir()
    assert (tmp_path / model / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not [p.name for p in tmp_path.iterdir() if ".old-" in p.name or ".tmp-" in p.name]


def _parse_failed(*a, **_k):
    return subprocess.CompletedProcess(a[0], 1, "", "parse failed")


def _parse_hang(*_a, **k):
    raise subprocess.TimeoutExpired("dbt parse", k.get("timeout"))


@pytest.mark.parametrize(
    ("run", "error"),
    [(_parse_failed, OSError), (_parse_hang, subprocess.TimeoutExpired)],
)
def test_failed_redeploy_keeps_workspace_on_previous_project(tmp_path, monkeypatch, run, error):
    spec = Spec.model_validate(SPEC)
    model = spec.transform.model_name
    _make_project(tmp_path / model, model, "old")
    deploy.write_workspace(str(tmp_path))
    before = (tmp_path / "workspace.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(deploy, "prepare_project", lambda root, _files: _make_project(root, model, "new"))
    monkeypatch.setattr(deploy.subprocess, "run", run)

    with pytest.raises(error):
        deploy.deploy_local(spec, {}, str(tmp_path))

    assert (tmp_path / "workspace.yaml").read_text(encoding="utf-8") == before
    assert _locations(tmp_path) == [f"./{model}"]
    assert (tmp_path / model / "marker.txt").read_text(encoding="utf-8") == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == [model, "workspace.yaml"]
