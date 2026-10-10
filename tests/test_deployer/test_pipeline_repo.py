"""Repo pipeline xuất từ app: kiểm commit Git và dbt thật, hoàn toàn offline."""

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.export_pipeline_repo import ROOT, export_pipeline_repo
from src.codegen.deploy import open_pull_request
from src.deployer.manifest import hash_files
from tests.test_codegen.test_deploy_pr import FOLDER, FakeGithub, approved_report


def git(repo, *args, input=None):
    return (
        subprocess.run(["git", *args], cwd=repo, input=input, capture_output=True, check=True, timeout=30)
        .stdout.decode()
        .strip()
    )


def save(repo, path, content):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content.encode())


def snapshot(repo):
    git(repo, "add", "--all")
    git(repo, "commit", "-m", "Bộ file test offline")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def pipeline_repo(tmp_path):
    repo = tmp_path / "pipelines"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/export_pipeline_repo.py"), str(repo)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    trusted = tmp_path / "trusted"
    export_pipeline_repo(trusted)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test offline")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "commit.gpgsign", "false")
    base = snapshot(repo)
    report, files = approved_report()
    github = FakeGithub()
    deployment = open_pull_request(report, files, github.client())
    for path, text in github.files_at(deployment.branch).items():
        if path.startswith(FOLDER + "/"):
            save(repo, path, text)
    head = snapshot(repo)
    return SimpleNamespace(repo=repo, trusted=trusted, base=base, head=head, model="fct_daily_orders", files=files)


def check(pr, head=None, base=None):
    return subprocess.run(
        [
            sys.executable,
            str(pr.trusted / "ci/check_pr.py"),
            "--repo",
            str(pr.repo),
            "--base",
            base or pr.base,
            "--head",
            head or pr.head,
        ],
        cwd=pr.repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def test_export_is_generated_byte_exact_and_preserves_other_files(tmp_path):
    source = tmp_path / "app"
    for name in ("requirements.txt", "src/deployer/manifest.py", "docs/deploy-pr.md"):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    shutil.copytree(ROOT / "deploy/pipeline-repo", source / "deploy/pipeline-repo")
    shutil.copytree(ROOT / "dbt_project", source / "dbt_project")
    for name in ("target/manifest.json", "logs/dbt.log", "models/cache.duckdb", "cache.duckdb.wal"):
        save(source / "dbt_project", name, "bỏ khỏi scaffold")
    repo = tmp_path / "exported"
    written = export_pipeline_repo(repo, source)
    required = {
        "README.md",
        ".github/workflows/ci.yml",
        "ci/manifest.py",
        "ci/check_pr.py",
        "ci/parse_project.py",
        "CODEOWNERS",
        "requirements-ci.txt",
    }
    assert required <= set(written)
    assert (repo / "ci/manifest.py").read_bytes() == (ROOT / "src/deployer/manifest.py").read_bytes()
    assert (repo / "README.md").read_bytes() == (ROOT / "docs/deploy-pr.md").read_bytes()
    scaffold = repo / "_scaffold/dbt_project"
    expected = {
        p.relative_to(ROOT / "dbt_project").as_posix() for p in (ROOT / "dbt_project").rglob("*") if p.is_file()
    }
    assert {p.relative_to(scaffold).as_posix() for p in scaffold.rglob("*") if p.is_file()} == expected
    for name in expected:
        assert (scaffold / name).read_bytes() == (ROOT / "dbt_project" / name).read_bytes()
    constraints = [
        line
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.startswith(("dbt-core", "dbt-duckdb"))
    ]
    assert (repo / "requirements-ci.txt").read_text(encoding="utf-8").splitlines()[1:] == constraints
    original = {name: (repo / name).read_bytes() for name in written}
    for name in written:
        (repo / name).write_bytes(b"sua tay")
    save(repo, "pipelines/existing/keep.txt", "giữ nguyên")
    save(repo, "unrelated.txt", "giữ nguyên")
    assert export_pipeline_repo(repo, source) == written
    assert {name: (repo / name).read_bytes() for name in written} == original
    assert (repo / "pipelines/existing/keep.txt").read_text(encoding="utf-8") == "giữ nguyên"
    assert (repo / "unrelated.txt").exists()


def test_workflow_reads_base_uses_minimal_permissions_and_fixed_check():
    workflow = yaml.safe_load((ROOT / "deploy/pipeline-repo/.github/workflows/ci.yml").read_text(encoding="utf-8"))
    events = workflow.get("on", workflow.get(True))
    assert set(events) == {"pull_request"}
    assert "edited" in events["pull_request"]["types"]
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["pipeline-ci"]
    assert job["name"] == "pipeline-ci"
    assert "default_branch" in job["if"]
    checkouts = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout")]
    assert all(s["with"]["fetch-depth"] == 0 and s["with"]["persist-credentials"] is False for s in checkouts)
    assert "base.sha" in checkouts[0]["with"]["ref"]
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    assert runs.index("check_pr.py") < runs.index("parse_project.py")
    assert "secrets." not in json.dumps(workflow)


def test_check_accepts_actual_deploy_pr_and_reads_commit_not_worktree(pipeline_repo):
    pr = pipeline_repo
    save(pr.repo, f"{FOLDER}/dbt_project/models/gold/{pr.model}.sql", "sửa chưa commit")
    result = check(pr)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == pr.model


@pytest.mark.parametrize("outside", ["README.md", "ci/check_pr.py", "pipelines/Bad/x.sql", "pipelines/model"])
def test_check_rejects_changes_outside_pipeline(pipeline_repo, outside):
    save(pipeline_repo.repo, outside, "ngoài phạm vi")
    result = check(pipeline_repo, snapshot(pipeline_repo.repo))
    assert result.returncode != 0
    assert outside in result.stderr and "mọi file thay đổi" in result.stderr


@pytest.mark.parametrize("kind", ["extra", "content", "missing", "manifest", "report", "delete"])
def test_check_rejects_unsigned_or_incomplete_files(pipeline_repo, kind):
    pr = pipeline_repo
    name = f"dbt_project/models/gold/{pr.model}.sql"
    path = f"{FOLDER}/{name}"
    if kind == "extra":
        path = f"{FOLDER}/extra.sql"
        save(pr.repo, path, "select 1")
        reason = "thừa"
    elif kind == "content":
        save(pr.repo, path, "select 1")
        reason = "sha256"
    elif kind == "delete":
        git(pr.repo, "rm", "-r", FOLDER)
        reason, path = "thiếu", FOLDER + "/manifest.json"
    else:
        if kind in ("manifest", "report"):
            path = f"{FOLDER}/{'manifest' if kind == 'manifest' else 'run_report'}.json"
        (pr.repo / path).unlink()
        reason = "thiếu"
    result = check(pr, snapshot(pr.repo), base=pr.head if kind == "delete" else None)
    assert result.returncode != 0
    assert reason in result.stderr
    assert Path(path).name in result.stderr


@pytest.mark.parametrize("mode", ["120000", "100755", "160000"])
@pytest.mark.parametrize("name", ["manifest.json", "run_report.json", "dbt_project/models/gold/fct_daily_orders.sql"])
def test_check_rejects_symlink_executable_submodule_without_os_symlinks(pipeline_repo, mode, name):
    pr = pipeline_repo
    sha = pr.head if mode == "160000" else git(pr.repo, "hash-object", "-w", "--stdin", input=b"target")
    path = f"{FOLDER}/{name}"
    git(pr.repo, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}")
    tree = git(pr.repo, "write-tree")
    head = git(pr.repo, "commit-tree", tree, "-p", pr.head, input=b"mode test\n")
    result = check(pr, head)
    assert result.returncode != 0
    assert path in result.stderr and "100644" in result.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("files_hash", "sha256:giả"),
        ("reviewer", "khac"),
        ("model_name", "fct_other"),
        ("version", True),
        ("approved_at", "sai"),
        ("files", []),
    ],
)
def test_check_rejects_forged_manifest(pipeline_repo, field, value):
    pr = pipeline_repo
    path = f"{FOLDER}/manifest.json"
    manifest = json.loads((pr.repo / path).read_text(encoding="utf-8"))
    manifest[field] = value
    save(pr.repo, path, json.dumps(manifest))
    result = check(pr, snapshot(pr.repo))
    assert result.returncode != 0
    assert path in result.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "khac"),
        ("version", 99),
        ("files_hash", "khac"),
        ("approval.reviewer", "khac"),
        ("approval.version", True),
        ("approval.files_hash", "khac"),
        ("approval.at", "2026-10-10T08:00:00Z"),
        ("approval.decision", "rejected"),
        ("status", "READY_FOR_REVIEW"),
        ("files", {}),
        ("spec", None),
    ],
)
def test_check_rejects_report_signature_mismatch(pipeline_repo, field, value):
    pr = pipeline_repo
    path = f"{FOLDER}/run_report.json"
    report = json.loads((pr.repo / path).read_text(encoding="utf-8"))
    if field.startswith("approval."):
        report["approval"][field.split(".")[1]] = value
    else:
        report[field] = value
    save(pr.repo, path, json.dumps(report))
    result = check(pr, snapshot(pr.repo))
    assert result.returncode != 0
    assert path in result.stderr


@pytest.mark.parametrize("text", ["{", "[]", '{"version":1,"version":2}'])
def test_check_rejects_bad_or_duplicate_json(pipeline_repo, text):
    path = FOLDER + "/manifest.json"
    save(pipeline_repo.repo, path, text)
    result = check(pipeline_repo, snapshot(pipeline_repo.repo))
    assert result.returncode != 0
    assert path in result.stderr and "JSON" in result.stderr


def test_check_uses_merge_base_and_catches_deleted_outside_path(pipeline_repo):
    pr = pipeline_repo
    git(pr.repo, "checkout", "-b", "base-ahead", pr.base)
    save(pr.repo, "README.md", "thay đổi chỉ có trên base")
    base = snapshot(pr.repo)
    assert check(pr, base=base).returncode == 0
    git(pr.repo, "checkout", "-b", "delete-outside", pr.head)
    git(pr.repo, "rm", "README.md")
    result = check(pr, snapshot(pr.repo))
    assert result.returncode != 0
    assert "README.md" in result.stderr


def test_parse_rendered_sample_with_real_dbt_offline(pipeline_repo):
    result = subprocess.run(
        [sys.executable, "ci/parse_project.py", pipeline_repo.model, "--head", pipeline_repo.head, "--timeout", "120"],
        cwd=pipeline_repo.repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=150,
    )
    assert result.returncode == 0, result.stderr
    assert "thành công" in result.stdout
    assert not (pipeline_repo.repo / "_scaffold/dbt_project/target").exists()


def test_parse_rejects_dbt_error_even_when_hashes_are_consistent(pipeline_repo):
    pr = pipeline_repo
    name = "dbt_project/models/gold/fct_daily_orders.sql"
    files = {**pr.files, name: "select * from {{ ref('model_khong_ton_tai') }}\n"}
    hashes, digest = hash_files(files)
    save(pr.repo, FOLDER + "/" + name, files[name])
    for filename in ("manifest.json", "run_report.json"):
        path = FOLDER + "/" + filename
        value = json.loads((pr.repo / path).read_text(encoding="utf-8"))
        value.update(files=hashes, files_hash=digest)
        if filename == "run_report.json":
            value["approval"]["files_hash"] = digest
        save(pr.repo, path, json.dumps(value))
    head = snapshot(pr.repo)
    assert check(pr, head).returncode == 0
    result = subprocess.run(
        [sys.executable, str(pr.trusted / "ci/parse_project.py"), pr.model, "--repo", str(pr.repo), "--head", head],
        cwd=pr.repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=150,
    )
    assert result.returncode != 0
    assert "dbt parse thất bại" in result.stderr and "model_khong_ton_tai" in result.stderr
    assert len(result.stderr) < 4500


def test_check_each_changed_pipeline_and_preserves_signed_crlf(pipeline_repo):
    pr = pipeline_repo
    model = "fct_daily_second"
    files = {"model.sql": "select 1\r\n-- có dấu\r\n"}
    hashes, digest = hash_files(files)
    original_manifest = json.loads((pr.repo / FOLDER / "manifest.json").read_text(encoding="utf-8"))
    original_report = json.loads((pr.repo / FOLDER / "run_report.json").read_text(encoding="utf-8"))
    original_manifest.update(model_name=model, files=hashes, files_hash=digest)
    original_report.update(files=hashes, files_hash=digest)
    original_report["approval"]["files_hash"] = digest
    original_report["spec"]["transform"]["model_name"] = model
    folder = f"pipelines/{model}"
    save(pr.repo, folder + "/manifest.json", json.dumps(original_manifest))
    save(pr.repo, folder + "/run_report.json", json.dumps(original_report))
    save(pr.repo, folder + "/model.sql", files["model.sql"])
    result = check(pr, snapshot(pr.repo))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [pr.model, model]
    save(pr.repo, folder + "/model.sql", "select 2\n")
    result = check(pr, snapshot(pr.repo))
    assert result.returncode != 0
    assert folder in result.stderr and "sha256" in result.stderr


@pytest.mark.parametrize("failure", ["error", "timeout", "success"])
def test_parse_limits_logs_timeout_env_and_uses_temporary_project(pipeline_repo, monkeypatch, failure):
    pr = pipeline_repo
    monkeypatch.syspath_prepend(str(pr.repo / "ci"))
    namespace = runpy.run_path(str(pr.repo / "ci/parse_project.py"))
    parse = namespace["parse_project"]
    monkeypatch.setitem(parse.__globals__, "commit", lambda *args: pr.head)
    monkeypatch.setitem(parse.__globals__, "pipeline_files", lambda *args: pr.files)
    monkeypatch.setenv("PIPELINE_GITHUB_TOKEN", "khong-truyen-vao-dbt")
    created = []

    def run(command, cwd, env, stdout, stderr, timeout):
        project = Path(cwd) / "dbt_project"
        created.append(project)
        assert (project / "models/gold/fct_daily_orders.sql").exists()
        assert not (Path(cwd) / "ingestion").exists()
        assert "PIPELINE_GITHUB_TOKEN" not in env
        assert env["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"
        assert command[-1] == str(project) and "--no-partial-parse" in command
        assert timeout == 3
        stdout.write(b"x" * 6000 + b"dbt error tail")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        return SimpleNamespace(returncode=1 if failure == "error" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    if failure == "success":
        parse(pr.repo, pr.head, pr.model, timeout=3)
    else:
        with pytest.raises(namespace["ManifestError"]) as error:
            parse(pr.repo, pr.head, pr.model, timeout=3)
        assert "dbt error tail" in str(error.value)
        assert len(str(error.value)) < 4200
        if failure == "timeout":
            assert "quá 3 giây" in str(error.value)
    assert created and not created[0].exists()
