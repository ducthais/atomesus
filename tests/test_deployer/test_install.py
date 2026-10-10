import subprocess

import pytest

from src.deployer import install
from src.deployer.manifest import ManifestError


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
