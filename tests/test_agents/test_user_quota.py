"""Hạn mức theo từng người dùng (ADR-0001 D10): tiền/ngày và số run/ngày, giữ nguyên trần chung."""

import shutil
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.agents import budget
from src.agents.nodes.common import AgentDeps
from src.api import routes
from src.chat import api as chat_api
from src.main import app
from src.pipeline import api, auth, store
from src.pipeline.settings import PipelineSettings
from src.policy.loader import POLICY_DIR, POLICY_FILES, PolicyError, load_policy
from src.policy.models import Policy
from src.run_report import FIXTURES_DIR, RunReport, quota, save_run_report

ENGINEER_B = "user_eng_b"
SMALL_SCAN = {"sources": {"orders": 1000}, "model_bytes": 1000, "bytes": 2000, "usd": 0.04, "estimated": True}


def _settings(tmp_path) -> PipelineSettings:
    config = PipelineSettings(_env_file=None, store_path=str(tmp_path / "runs.sqlite3"))
    store.create_user(
        config.store_path,
        user_id=ENGINEER_B,
        username="eng.b",
        name="eng.b",
        role="Engineer",
        password_hash=auth.hash_password("StrongPass9"),
        created_at=time.time(),
        email="eng.b@example.com",
    )
    return config


def _run(store_path: str, run_id: str, owner: str, usd: float = 0.0, **update) -> RunReport:
    """Run tạo hôm nay của `owner`, đã ghi `usd` vào sổ chi phí."""
    base = RunReport.model_validate_json((FIXTURES_DIR / "ready_for_review.json").read_text(encoding="utf-8"))
    report = base.model_copy(
        update={
            "run_id": run_id,
            "created_by": owner,
            "created_at": datetime.now(UTC),
            "cost": base.cost.model_copy(update={"usd": usd, "llm_usd": 0.0}),
            **update,
        }
    )
    save_run_report(store_path, report)
    return report


def _with_per_user(policy: Policy, **per_user) -> Policy:
    budgets = policy.budgets.model_copy(update={"per_user": policy.budgets.per_user.model_copy(update=per_user)})
    return policy.model_copy(update={"budgets": budgets})


def _reserve(config, run_id: str, usd: float, policy: Policy, category: str = "llm:plan:1"):
    budgets = policy.budgets
    return budget.reserve_daily(
        config, run_id, 1, category, usd, budgets.per_day.max_usd, budgets.per_user.max_usd_per_day
    )


def test_budgets_yml_has_per_user_block_and_defaults_when_missing(tmp_path):
    per_user = load_policy().budgets.per_user
    assert (per_user.max_usd_per_day, per_user.max_runs_per_day) == (0.30, 20)

    for name in POLICY_FILES:
        shutil.copy(POLICY_DIR / f"{name}.yml", tmp_path / f"{name}.yml")
    text = (tmp_path / "budgets.yml").read_text(encoding="utf-8")
    start, end = text.index("per_user:"), text.index("optimizer:")
    (tmp_path / "budgets.yml").write_text(text[:start] + text[end:], encoding="utf-8")
    defaults = load_policy(tmp_path).budgets.per_user
    assert (defaults.max_usd_per_day, defaults.max_runs_per_day) == (0.30, 20)

    (tmp_path / "budgets.yml").write_text(
        text.replace("max_usd_per_day: 0.30", "max_usd_per_day: 0.01"), encoding="utf-8"
    )
    with pytest.raises(PolicyError, match="per_user.max_usd_per_day"):
        load_policy(tmp_path)


def test_two_users_have_separate_budgets(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a1", "minh", usd=0.28)
    _run(config.store_path, "b1", ENGINEER_B)

    blocked = _reserve(config, "a1", 0.05, policy)
    allowed = _reserve(config, "b1", 0.05, policy)

    assert blocked == (False, pytest.approx(0.28), pytest.approx(0.28))
    assert allowed == (True, pytest.approx(0.28), pytest.approx(0.0))
    assert quota.spent_today(config.store_path, "minh") == pytest.approx(0.28)
    assert quota.spent_today(config.store_path, ENGINEER_B) == pytest.approx(0.05)


def test_personal_cap_blocks_even_when_global_cap_has_room(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a1", "minh", usd=0.28)

    result, errors, _estimated = budget.check(
        None, policy, config, estimate_cost=lambda *_: SMALL_SCAN, reserve=budget.reserve_daily, run_id="a1"
    )

    assert result.result == "fail" and len(errors) == 1
    assert "trần cá nhân" in errors[0] and "per_user.max_usd_per_day = 0.3" in errors[0]
    assert "per_day.max_usd" not in errors[0]
    assert result.observed["user_spent_today_usd"] == pytest.approx(0.28)
    assert budget.spent_today(config) == pytest.approx(0.28), "bị chặn thì không giữ chỗ"


def test_global_cap_blocks_everyone(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "b1", ENGINEER_B)
    _run(config.store_path, "a1", "minh")
    assert budget.reserve_daily(config, "legacy-run", 1, "scan", 1.98, 2.0)[0]  # run không rõ chủ: chỉ trần chung

    for run_id in ("a1", "b1"):
        result, errors, _estimated = budget.check(
            None, policy, config, estimate_cost=lambda *_: SMALL_SCAN, reserve=budget.reserve_daily, run_id=run_id
        )
        assert result.result == "fail"
        assert "trần chung" in errors[0] and "per_day.max_usd" in errors[0]
        assert "trần cá nhân" not in "; ".join(errors)


def test_llm_call_blocked_by_personal_cap_names_the_cap(tmp_path):
    from src.agents.nodes.common import StopError, call_llm

    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a1", "minh", usd=0.28)
    deps = AgentDeps(
        llm=lambda *_: pytest.fail("không được gọi LLM"),
        policy=policy,
        settings=config,
        reserve_daily=budget.reserve_daily,
    )

    with pytest.raises(StopError) as stopped:
        call_llm({"run_id": "a1", "version": 1}, deps, "plan", "cheap", "system", "user")

    assert stopped.value.status == "BLOCKED_BUDGET"
    assert "per_user.max_usd_per_day" in stopped.value.trace["error"]


def test_system_run_counts_for_the_pipeline_owner(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a-dep", "minh")
    _run(config.store_path, "sys", "system", trigger="regression", parent_run_id="a-dep")

    assert _reserve(config, "sys", 0.2, policy)[0]

    assert quota.spent_today(config.store_path, "minh") == pytest.approx(0.2)
    assert quota.spent_today(config.store_path, ENGINEER_B) == 0
    usage = quota.usage(config.store_path, auth.USERS["minh"], policy.budgets)
    assert usage["runs"]["used"] == 2 and usage["usd"]["used"] == pytest.approx(0.2)
    assert _reserve(config, "sys", 0.15, policy, category="llm:plan:2")[0] is False


def test_concurrent_reservations_of_one_user_never_exceed_the_personal_cap(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    for run_id in ("a1", "a2"):
        _run(config.store_path, run_id, "minh")
    barrier = threading.Barrier(3)
    outcomes = []

    def reserve(run_id):
        barrier.wait()
        outcomes.append(_reserve(config, run_id, 0.2, policy)[0])

    threads = [threading.Thread(target=reserve, args=(run_id,)) for run_id in ("a1", "a2")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [False, True]
    assert quota.spent_today(config.store_path, "minh") == pytest.approx(0.2)


def test_batch_estimate_and_reservation_respect_the_personal_cap(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a1", "minh", usd=0.25)

    estimate = budget.estimate_batch(2, policy, config, "minh")
    run_ids = quota.claim_runs(config.store_path, auth.USERS["minh"], policy.budgets, 2)
    reserved, _spent, user_spent = budget.reserve_batch_daily(
        config, [(run_id, 1, "llm:plan:1", 0.05) for run_id in run_ids], 2.0, "minh", 0.30
    )

    assert estimate["allowed"] is False and estimate["per_user"]["spent_today_usd"] == pytest.approx(0.25)
    assert reserved is False and user_spent == pytest.approx(0.25)
    assert budget.estimate_batch(2, policy, config, ENGINEER_B)["allowed"] is True


def test_run_slots_are_atomic_and_admin_is_not_limited(tmp_path):
    config = _settings(tmp_path)
    budgets = _with_per_user(load_policy(), max_runs_per_day=1).budgets
    barrier = threading.Barrier(3)
    outcomes = []

    def claim():
        barrier.wait()
        try:
            quota.claim_runs(config.store_path, auth.USERS["minh"], budgets)
            outcomes.append(True)
        except Exception as exc:
            outcomes.append(getattr(exc, "status_code", exc))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(outcomes, key=str) == [429, True]
    for _ in range(3):
        assert len(quota.claim_runs(config.store_path, auth.USERS["admin"], budgets)) == 1


@pytest.fixture
def quota_env(client, tmp_path, monkeypatch):
    """Engineer minh đã dùng hết 1 lượt run trong ngày; mọi đường tạo run không được chạy agent."""
    config = _settings(tmp_path)
    policy = _with_per_user(load_policy(), max_runs_per_day=1)
    _run(config.store_path, "a1", "minh", usd=0.1)
    deps = AgentDeps(llm=lambda *_: pytest.fail("không được gọi LLM"), policy=policy, settings=config)
    app.dependency_overrides[auth.settings] = lambda: config
    app.dependency_overrides[routes.agent_deps] = lambda: deps
    environment = SimpleNamespace(app_env="test")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "load_policy", lambda: policy)
    monkeypatch.setattr(routes, "start_agent", lambda *args, **kwargs: pytest.fail("không được tạo run"))
    monkeypatch.setattr(chat_api, "plan_agent", lambda *args, **kwargs: pytest.fail("không được lập kế hoạch"))
    try:
        yield SimpleNamespace(client=client, config=config)
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)


async def _login(client, username: str, password: str) -> None:
    await client.delete("/api/v1/pipeline/session")
    response = await client.post("/api/v1/pipeline/session", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/agent/runs", {"request": "Doanh thu theo ngày"}),
        ("/api/v1/agent/runs/batch", {"requests": [{"request": "A"}, {"request": "B"}]}),
        ("/api/v1/chat", {"message": "Doanh thu theo ngày"}),
        (
            "/api/v1/pipeline/runs",
            {"title": "Làm sạch đơn hàng", "columns": [{"from": "id", "as": "id", "type": "bigint"}]},
        ),
    ],
)
async def test_creating_a_run_after_the_daily_run_limit_returns_429(quota_env, path, body):
    await _login(quota_env.client, "engineer", "Engineer@123")

    response = await quota_env.client.post(path, json=body)

    assert response.status_code == 429, response.text
    assert response.json()["detail"] == "Bạn đã dùng hết 1 run hôm nay, thử lại sau 0:00 UTC."
    assert int(response.headers["retry-after"]) > 0
    usage = (await quota_env.client.get("/api/v1/pipeline/quota")).json()
    assert usage["runs"] == {"used": 1, "limit": 1, "remaining": 0}


@pytest.mark.asyncio
async def test_quota_api_shows_only_the_callers_own_usage(quota_env):
    _run(quota_env.config.store_path, "b1", ENGINEER_B, usd=0.2)
    await _login(quota_env.client, "engineer", "Engineer@123")

    mine = (await quota_env.client.get("/api/v1/pipeline/quota")).json()

    assert mine["limited"] is True
    assert mine["usd"] == {"used": pytest.approx(0.1), "limit": 0.3, "remaining": pytest.approx(0.2)}
    assert set(mine) == {"day", "resets_at", "limited", "usd", "runs"}

    await _login(quota_env.client, "eng.b", "StrongPass9")
    theirs = (await quota_env.client.get("/api/v1/pipeline/quota")).json()
    assert theirs["usd"]["used"] == pytest.approx(0.2) and theirs["runs"]["used"] == 1

    await _login(quota_env.client, "admin", "Admin@123")
    admin = (await quota_env.client.get("/api/v1/pipeline/quota")).json()
    assert admin["limited"] is False and admin["usd"]["limit"] is None and admin["runs"]["limit"] is None


@pytest.mark.asyncio
async def test_changing_budgets_yml_changes_the_limits(quota_env, tmp_path, monkeypatch):
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    for name in POLICY_FILES:
        shutil.copy(POLICY_DIR / f"{name}.yml", policy_dir / f"{name}.yml")
    text = (policy_dir / "budgets.yml").read_text(encoding="utf-8")
    text = text.replace("max_usd_per_day: 0.30", "max_usd_per_day: 0.10").replace(
        "max_runs_per_day: 20", "max_runs_per_day: 3"
    )
    (policy_dir / "budgets.yml").write_text(text, encoding="utf-8")
    edited = load_policy(policy_dir)
    monkeypatch.setattr(api, "load_policy", lambda: edited)
    await _login(quota_env.client, "engineer", "Engineer@123")

    usage = (await quota_env.client.get("/api/v1/pipeline/quota")).json()

    assert usage["usd"]["limit"] == 0.1 and usage["runs"]["limit"] == 3
    # 0.1 USD đã dùng chạm đúng trần mới: lần gọi LLM kế tiếp bị chặn, với trần cũ (0.30) thì còn được.
    assert _reserve(quota_env.config, "a1", 0.05, edited)[0] is False
    assert _reserve(quota_env.config, "a1", 0.05, load_policy())[0] is True
    assert len(quota.claim_runs(quota_env.config.store_path, auth.USERS["minh"], edited.budgets, 2)) == 2
