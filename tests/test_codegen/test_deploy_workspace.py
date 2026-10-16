"""Cài lại pipeline trên code location hợp nhất: `.generation` chỉ đổi khi bộ project đang chạy thật sự đổi.

Supervisor Dagster nạp lại code location mỗi khi `.generation` đổi (scripts/dagster_local.py); loader và Day 2 bỏ qua
thư mục tạm / sao lưu của install_project (src/dagster_runtime/contract.py: is_project_dir).
"""

import subprocess
from pathlib import Path

import pytest

from src.dagster_runtime.contract import is_project_dir
from src.deployer import install
from src.spec.models import Spec
from tests.test_agents.test_graph import SPEC


def _make_project(root: Path, model: str, marker: str) -> None:
    generated = root / "infra" / "dagster" / "generated"
    generated.mkdir(parents=True)
    (generated / f"{model}_asset.py").write_text(f"# {marker}\n", encoding="utf-8")
    (root / "marker.txt").write_text(marker, encoding="utf-8")


@pytest.mark.parametrize(
    "name,live", [("sales", True), ("sales.old-abc", False), ("sales.tmp-abc", False), (".hidden", False)]
)
def test_only_live_project_dirs_are_loaded(name, live):
    assert is_project_dir(name) is live


def test_redeploy_existing_pipeline_marks_a_new_generation(tmp_path, monkeypatch):
    spec = Spec.model_validate(SPEC)
    model = spec.transform.model_name
    _make_project(tmp_path / model, model, "old")
    before = install.write_generation(str(tmp_path)).read_text(encoding="utf-8")
    monkeypatch.setattr(install, "prepare_project", lambda root, _files: _make_project(root, model, "new"))
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""))

    install.install_project(model, {}, str(tmp_path))

    assert (tmp_path / ".generation").read_text(encoding="utf-8") != before
    assert (tmp_path / model / "marker.txt").read_text(encoding="utf-8") == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".generation", model]


def _parse_failed(*a, **_k):
    return subprocess.CompletedProcess(a[0], 1, "", "parse failed")


def _parse_hang(*_a, **k):
    raise subprocess.TimeoutExpired("dbt parse", k.get("timeout"))


@pytest.mark.parametrize(
    ("run", "error"),
    [(_parse_failed, OSError), (_parse_hang, subprocess.TimeoutExpired)],
)
def test_failed_redeploy_keeps_previous_project_and_generation(tmp_path, monkeypatch, run, error):
    # dbt parse lỗi xảy ra trước khi đổi thư mục: không có gì để nạp lại, Dagster không bị đụng tới.
    spec = Spec.model_validate(SPEC)
    model = spec.transform.model_name
    _make_project(tmp_path / model, model, "old")
    before = install.write_generation(str(tmp_path)).read_text(encoding="utf-8")
    monkeypatch.setattr(install, "prepare_project", lambda root, _files: _make_project(root, model, "new"))
    monkeypatch.setattr(install.subprocess, "run", run)

    with pytest.raises(error):
        install.install_project(model, {}, str(tmp_path))

    assert (tmp_path / ".generation").read_text(encoding="utf-8") == before
    assert (tmp_path / model / "marker.txt").read_text(encoding="utf-8") == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".generation", model]
