"""Tám bất biến của hệ thống (SPEC §19), mỗi bất biến ít nhất một test.

Bất biến 8 (mọi thay đổi lên production đi qua pull request) chưa đạt: deploy hiện ghi thẳng ra Dagster local, giai
đoạn Deploy làm sau cùng (new_task.md). Test của nó để `xfail(strict=True)`: khi có pull request thật, test sẽ báo XPASS
để nhớ bỏ đánh dấu.
"""

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.graph import build_graph
from src.agents.service import run_agent
from src.codegen import sandbox
from src.pipeline import auth
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import get_run_report
from src.run_report.store import get_run_files
from src.spec import SPEC_FIXTURES_DIR, Spec
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps, with_column

ROOT = Path(__file__).resolve().parents[1]


def _spec() -> Spec:
    return Spec.model_validate_json((SPEC_FIXTURES_DIR / "spec_01_pg_daily_orders.json").read_text(encoding="utf-8"))


def _python_files(*folders: str) -> list[Path]:
    return [p for folder in folders for p in (ROOT / folder).rglob("*.py") if "__pycache__" not in p.parts]


# 1. Sandbox mới cho mỗi run, xóa sau khi xong ------------------------------------------------------------------


def test_1_every_run_gets_a_fresh_sandbox_that_is_deleted(monkeypatch):
    seen: list[Path] = []

    def record(path: Path, files: dict) -> None:
        seen.append(path)
        assert path.exists()
        raise sandbox._StopError("INFRA_ERROR", "el_run", "dừng ngay sau khi ghi nhận thư mục sandbox")

    monkeypatch.setattr(sandbox, "prepare_project", record)
    policy, settings = load_policy(), PipelineSettings(_env_file=None)
    for _ in range(2):
        assert sandbox.run_in_sandbox(_spec(), {}, policy, settings).status == "INFRA_ERROR"
    assert len(seen) == 2 and seen[0] != seen[1], "mỗi run một thư mục riêng"
    assert not any(path.exists() for path in seen), "sandbox bị xóa kể cả khi run lỗi"


# 2. Không có credential production trong môi trường agent -------------------------------------------------------


def test_2_generated_code_runs_without_backend_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bi-mat")
    monkeypatch.setenv("DAGSTER_PRODUCTION_TOKEN", "prod-bi-mat")
    settings = PipelineSettings(_env_file=None, database_url="postgresql://reader:x@127.0.0.1:5433/demo")
    env = sandbox._env(Path("sandbox"), _spec(), load_policy(), settings)
    fixed = {
        "WAREHOUSE_PATH",
        "WAREHOUSE_TIMEZONE",
        "DLT_DATA_DIR",
        "RUNTIME__LOG_LEVEL",
        "DBT_SEND_ANONYMOUS_USAGE_STATS",
        "DO_NOT_TRACK",
        "PYTHONIOENCODING",
    }
    unexpected = {
        k for k in env if k not in fixed and k not in sandbox.PASSTHROUGH_ENV and not k.startswith("SOURCES__")
    }
    assert not unexpected, f"biến lạ lọt vào tiến trình chạy code sinh ra: {unexpected}"
    assert "sk-bi-mat" not in env.values() and "prod-bi-mat" not in env.values()


def test_2_backend_settings_have_no_production_credentials():
    fields = set(PipelineSettings.model_fields)
    assert not {f for f in fields if re.search(r"prod|merge|github|dagster_token", f)}, fields


# 3 + 6. File deploy truy về run_id và chữ ký; chữ ký phải khớp files_hash ---------------------------------------


@pytest.mark.asyncio
async def test_3_and_6_only_the_signed_files_are_deployed(client, tmp_path, monkeypatch):
    from src.main import app

    store = str(tmp_path / "runs.sqlite3")
    report = run_agent("Doanh thu theo ngày", "minh", deps(FakeLLM(PLAN, SPEC)), store)
    assert report.status == "READY_FOR_REVIEW"
    config = PipelineSettings(_env_file=None, store_path=store, deploy_dir=str(tmp_path / "deploy"))
    app.dependency_overrides[auth.settings] = lambda: config
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(app_env="test"))
    try:
        await client.post("/api/v1/pipeline/session", json={"username": "reviewer", "password": "Reviewer@123"})
        url = f"/api/v1/runs/{report.run_id}/approve"
        wrong = await client.post(url, json={"files_hash": "sha256:khac", "comment": ""})
        assert wrong.status_code == 409 and not (tmp_path / "deploy").exists(), "chữ ký sai bộ file: không deploy"
        done = await client.post(url, json={"files_hash": report.files_hash, "comment": "ok"})
        assert done.status_code == 200 and done.json()["status"] == "DEPLOYED"
    finally:
        app.dependency_overrides.pop(auth.settings, None)
    signed = get_run_report(store, report.run_id)
    assert signed.approval.files_hash == signed.files_hash and signed.approval.reviewer != signed.created_by
    project = tmp_path / "deploy" / signed.spec["transform"]["model_name"]
    for path, content in get_run_files(store, report.run_id, signed.version).items():
        assert (project / path).read_text(encoding="utf-8") == content, f"{path} khác bộ file đã ký"


# 4. Không node LLM nào chạy lệnh shell ---------------------------------------------------------------------------


SHELL = {"subprocess", "pty", "pexpect"}
SHELL_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "spawnv"),
    ("asyncio", "create_subprocess_exec"),
}


@pytest.mark.parametrize("path", _python_files("src/agents", "src/chat"), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_4_agent_and_chat_code_cannot_run_shell_commands(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & SHELL, ast.dump(node)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in SHELL, ast.dump(node)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert (node.value.id, node.attr) not in SHELL_CALLS, f"{path.name}: {node.value.id}.{node.attr}"


# 5. Mọi phiên bản Spec đều nằm trong spec_history ----------------------------------------------------------------


def test_5_every_spec_version_is_kept():
    wrong = with_column(SPEC, 2, **{"from": "khong_co_cot_nay"})
    state = build_graph(deps(FakeLLM(PLAN, wrong, SPEC))).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Doanh thu theo ngày", "llm_calls": 0, "error_trace": []}
    )
    history = state["spec_history"]
    assert "khong_co_cot_nay" in json.dumps(history[0]["spec"]) and history[0]["errors"], "bản sai cũng được giữ"
    # Spec chạy thật (sau cả bước P5 đổi lịch hằng ngày → hằng giờ) phải là phiên bản mới nhất trong lịch sử.
    assert history[-1]["spec"] == state["spec"] and state["spec"]["schedule"]["cron"] == "0 * * * *"
    assert "P5 đổi lịch" in history[-1]["reason"]


# 7. llm_calls không vượt trần, trần đọc từ budgets.yml -------------------------------------------------------------


@pytest.mark.parametrize("limit", [2, 3])
def test_7_llm_calls_never_exceed_the_policy_limit(limit):
    policy = load_policy()
    per_run = policy.budgets.per_run.model_copy(update={"max_llm_calls": limit, "max_spec_attempts": 10})
    policy = policy.model_copy(update={"budgets": policy.budgets.model_copy(update={"per_run": per_run})})
    wrong = with_column(SPEC, 2, **{"from": "khong_co_cot_nay"})
    llm = FakeLLM(PLAN, *[wrong] * 10)
    state = build_graph(deps(llm, policy=policy)).invoke(
        {"run_id": "r1", "version": 1, "user_request": "Doanh thu theo ngày", "llm_calls": 0, "error_trace": []}
    )
    assert state["status"] == "NEEDS_HUMAN" and state["llm_calls"] == len(llm.calls) == limit
    assert "max_llm_calls" in state["error_trace"][-1]["error"]


def test_7_limit_is_not_hardcoded():
    pattern = re.compile(r"llm_calls[^\n]*(>=|>|<=|<|==)\s*\d")
    hits = [
        f"{p.relative_to(ROOT)}:{i}"
        for p in _python_files("src")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not hits, hits


# 8. Mọi thay đổi lên production qua pull request; agent không có quyền merge ----------------------------------------


MERGE = re.compile(r"git\s+merge|gh\s+pr\s+merge|pulls/[^\"']*/merge|merge_pull|\.merge_pr\(", re.IGNORECASE)


def test_8_system_has_no_way_to_merge():
    hits = [
        str(p.relative_to(ROOT)) for p in _python_files("src", "scripts") if MERGE.search(p.read_text(encoding="utf-8"))
    ]
    assert not hits, f"code có thao tác merge: {hits}"


@pytest.mark.xfail(
    strict=True, reason="Chưa làm: duyệt xong vẫn ghi thẳng ra Dagster local (giai đoạn Deploy, SPEC §9.5)"
)
def test_8_approval_opens_a_pull_request_instead_of_writing_dagster():
    from src.codegen import deploy

    assert hasattr(deploy, "open_pull_request") and not hasattr(deploy, "deploy_local")
