"""Hạn mức theo từng người dùng (ADR-0001 D10): tiền/ngày và số run/ngày, giữ nguyên trần chung."""

import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.agents import budget
from src.agents.nodes.common import AgentDeps
from src.api import monitor, routes
from src.chat import api as chat_api
from src.main import app
from src.pipeline import api, auth, store
from src.pipeline.settings import PipelineSettings
from src.policy.loader import POLICY_DIR, POLICY_FILES, PolicyError, load_policy
from src.policy.models import Policy
from src.run_report import FIXTURES_DIR, RunReport, quota, save_run_report
from src.run_report.store import _database

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
async def test_optimize_after_the_daily_run_limit_returns_429_before_any_work(quota_env, monkeypatch):
    parent = _run(quota_env.config.store_path, "a1", "minh", usd=0.1)  # bản đã deploy (deployed_report giả)
    monkeypatch.setattr(monitor.runs, "deployed_report", lambda store_path, model: parent)
    monkeypatch.setattr(monitor, "model_visible", lambda *_: True)
    monkeypatch.setattr(monitor, "start_manual_optimize", lambda *args, **kwargs: pytest.fail("không được tối ưu"))
    app.dependency_overrides[monitor.monitor_deps] = lambda: app.dependency_overrides[routes.agent_deps]()
    try:
        await _login(quota_env.client, "engineer", "Engineer@123")
        response = await quota_env.client.post("/api/v1/pipelines/orders_model/optimize")
    finally:
        app.dependency_overrides.pop(monitor.monitor_deps, None)

    assert response.status_code == 429, response.text
    assert response.json()["detail"] == "Bạn đã dùng hết 1 run hôm nay, thử lại sau 0:00 UTC."
    assert quota.usage(quota_env.config.store_path, auth.USERS["minh"], load_policy().budgets)["runs"]["used"] == 1


def _slots(store_path: str) -> list[str]:
    with _database(store_path) as conn:
        return [row[0] for row in conn.execute("SELECT run_id FROM run_slots")]


@pytest.fixture
def open_env(client, tmp_path, monkeypatch):
    """Engineer minh còn đủ 2 lượt run trong ngày; agent thật với LLM giả."""
    config = _settings(tmp_path)
    policy = _with_per_user(load_policy(), max_runs_per_day=2)
    environment = SimpleNamespace(app_env="test")
    monkeypatch.setattr(auth, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "get_settings", lambda: environment)
    monkeypatch.setattr(api, "load_policy", lambda: policy)
    app.dependency_overrides[auth.settings] = lambda: config
    try:
        yield SimpleNamespace(client=client, config=config, policy=policy)
    finally:
        app.dependency_overrides.pop(auth.settings, None)
        app.dependency_overrides.pop(routes.agent_deps, None)
        app.dependency_overrides.pop(monitor.monitor_deps, None)


@pytest.mark.asyncio
async def test_active_chat_run_counts_once_and_the_remaining_run_is_accepted(open_env):
    from tests.test_agents.test_graph import PLAN, FakeLLM, deps

    store_path, minh = open_env.config.store_path, auth.USERS["minh"]
    seen = {}

    def llm(tier, system, user):
        # Lúc planner gọi LLM, run của chat đã được lưu (GENERATING) mà lượt giữ vẫn còn: chỉ tính một run.
        seen["usage"] = quota.usage(store_path, minh, open_env.policy.budgets)["runs"]
        seen["claimed"] = quota.claim_runs(store_path, minh, open_env.policy.budgets)
        return FakeLLM(PLAN)(tier, system, user)

    app.dependency_overrides[routes.agent_deps] = lambda: deps(llm, policy=open_env.policy)
    await _login(open_env.client, "engineer", "Engineer@123")

    response = await open_env.client.post("/api/v1/chat", json={"message": "Doanh thu theo ngày"})

    assert response.status_code == 200, response.text
    assert seen["usage"] == {"used": 1, "limit": 2, "remaining": 1}
    assert len(seen["claimed"]) == 1
    run_id = response.json()["run_id"]
    assert sorted(_slots(store_path)) == sorted([run_id, *seen["claimed"]]), "run chat dùng đúng id của lượt đã giữ"
    assert quota.usage(store_path, minh, open_env.policy.budgets)["runs"]["used"] == 2


@pytest.mark.asyncio
async def test_optimize_uses_the_claimed_run_slot(open_env, monkeypatch):
    from src.agents import manual_optimize
    from tests.test_agents.test_graph import FakeLLM, deps

    store_path = open_env.config.store_path
    parent = _run(store_path, "a1", "minh")  # bản đã deploy (deployed_report giả)
    monkeypatch.setattr(monitor.runs, "deployed_report", lambda store_path, model: parent)
    monkeypatch.setattr(monitor, "model_visible", lambda *_: True)
    real_start = manual_optimize.start_manual_optimize
    monkeypatch.setattr(monitor, "start_manual_optimize", lambda *args: (real_start(*args)[0], lambda: None))
    app.dependency_overrides[monitor.monitor_deps] = lambda: deps(FakeLLM(), policy=open_env.policy)
    await _login(open_env.client, "engineer", "Engineer@123")

    response = await open_env.client.post("/api/v1/pipelines/orders_model/optimize")
    exhausted = await open_env.client.post("/api/v1/pipelines/orders_model/optimize")

    assert response.status_code == 202, response.text
    assert _slots(store_path) == [response.json()["run_id"]]
    assert exhausted.status_code == 429, exhausted.text
    assert quota.usage(store_path, auth.USERS["minh"], open_env.policy.budgets)["runs"]["used"] == 2


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


class PricedLLM:
    """LLM giả khai báo model như openai_llm; trả đủ trần token đầu ra (trường hợp tốn nhất), chờ nhau ở `barrier`."""

    def __init__(self, model: str = "gpt-4o", barrier: threading.Barrier | None = None):
        self.models = {"cheap": model, "strong": model}
        self.barrier, self.limits = barrier, []

    def __call__(self, tier, system, user, max_output_tokens=None):
        from src.agents.llm import LLMReply

        self.limits.append(max_output_tokens)
        if self.barrier:
            self.barrier.wait(timeout=5)
        text = '{"ok": true}'
        return LLMReply(text, self.models[tier], len(system) + len(user), max_output_tokens)


def _settle(store_path: str, report: RunReport, reply, policy: Policy) -> None:
    """Ghi run sau lần gọi LLM: chi phí thật vào sổ, khoản giữ chỗ của lần gọi được nhả (như agent lưu báo cáo)."""
    from src.agents.nodes.common import llm_usage
    from src.run_report.models import LlmCall

    usage = llm_usage(reply, "plan", "cheap", policy)
    save_run_report(
        store_path,
        report.model_copy(
            update={
                "llm_calls": 1,
                "llm_usage": [LlmCall.model_validate(usage)],
                "cost": report.cost.model_copy(update={"llm_usd": usage["usd"]}),
            }
        ),
    )


def test_overlapping_llm_calls_settle_within_the_personal_cap(tmp_path):
    from src.agents.nodes.common import StopError, call_llm, llm_bound

    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a0", "minh", usd=0.20)
    reports = {run_id: _run(config.store_path, run_id, "minh") for run_id in ("a1", "a2")}
    llm = PricedLLM()
    deps = AgentDeps(llm=llm, policy=policy, settings=config, reserve_daily=budget.reserve_daily)
    bound, _kwargs = llm_bound(deps, "plan", "cheap", "system", "user")
    assert 0.05 < bound <= 0.10, "cận trên một lần gọi gpt-4o vượt per_run.max_usd; còn 0.10 USD chỉ đủ một lần"
    start, replies = threading.Barrier(3), {}

    def call(run_id):
        start.wait(timeout=5)
        try:
            state = {"run_id": run_id, "version": reports[run_id].version}
            replies[run_id] = call_llm(state, deps, "plan", "cheap", "system", "user")
        except StopError as exc:
            replies[run_id] = exc

    threads = [threading.Thread(target=call, args=(run_id,)) for run_id in reports]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join()

    called = {run_id: reply for run_id, reply in replies.items() if not isinstance(reply, StopError)}
    stopped = [reply for reply in replies.values() if isinstance(reply, StopError)]
    assert len(called) == 1 and len(stopped) == 1
    assert stopped[0].status == "BLOCKED_BUDGET" and "per_user.max_usd_per_day" in stopped[0].trace["error"]
    assert llm.limits == [policy.budgets.per_run.max_output_tokens], "mọi lần gọi nhà cung cấp đều kèm trần token ra"
    for run_id, reply in called.items():
        _settle(config.store_path, reports[run_id], reply, policy)
    settled = quota.spent_today(config.store_path, "minh")
    assert 0.20 < settled <= 0.20 + bound <= policy.budgets.per_user.max_usd_per_day


def test_llm_call_tops_up_a_smaller_batch_reservation_and_refuses_unpriced_models(tmp_path):
    from src.agents.nodes.common import StopError, call_llm, llm_bound

    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "a1", "minh")
    assert budget.reserve_batch_daily(config, [("a1", 1, "llm:plan:1", 0.05)], 2.0, "minh", 0.30)[0]
    deps = AgentDeps(llm=PricedLLM(), policy=policy, settings=config, reserve_daily=budget.reserve_daily)

    call_llm({"run_id": "a1", "version": 1}, deps, "plan", "cheap", "system", "user")

    bound, _kwargs = llm_bound(deps, "plan", "cheap", "system", "user")
    assert quota.spent_today(config.store_path, "minh") == pytest.approx(bound), (
        "giữ thêm phần chênh, không giữ hai lần"
    )

    unpriced = AgentDeps(llm=PricedLLM("model-chua-co-gia"), policy=policy, settings=config)
    with pytest.raises(StopError) as stopped:
        call_llm({"run_id": "a1", "version": 1, "llm_calls": 1}, unpriced, "plan", "cheap", "system", "user")
    assert stopped.value.status == "BLOCKED_BUDGET" and "pricing.llm" in stopped.value.trace["error"]
    assert unpriced.llm.limits == []


def _reserved_yesterday(store_path: str, run_id: str, usd: float, category: str = "llm:plan:1") -> None:
    """Khoản giữ chỗ còn lại từ hôm qua (vd mẻ giữ trước 0:00 UTC, chạy sau 0:00)."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with _database(store_path) as conn:
        conn.execute("INSERT INTO budget_reservations VALUES (?, 1, ?, ?, ?)", (run_id, category, usd, yesterday))


def _reservation_day(store_path: str, run_id: str) -> tuple[float, str]:
    with _database(store_path) as conn:
        usd, at = conn.execute(
            "SELECT usd, reserved_at FROM budget_reservations WHERE run_id = ?", (run_id,)
        ).fetchone()
    return usd, at[:10]


def test_previous_day_reservation_is_rebooked_against_todays_personal_cap(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    today = datetime.now(UTC).date().isoformat()
    _run(config.store_path, "a0", "minh", usd=0.299)
    _run(config.store_path, "a1", "minh")
    _run(config.store_path, "a2", "minh")
    _reserved_yesterday(config.store_path, "a1", 0.05)
    _reserved_yesterday(config.store_path, "a2", 0.05)

    # 0.299 + 0.005 > 0.30: khoản 0.05 của hôm qua không được coi là đã giữ cho hôm nay.
    assert _reserve(config, "a1", 0.005, policy) == (False, pytest.approx(0.299), pytest.approx(0.299))
    assert budget.reserve_batch_daily(config, [("a2", 1, "llm:plan:1", 0.005)], 2.0, "minh", 0.30)[0] is False
    assert quota.spent_today(config.store_path, "minh") == pytest.approx(0.299)

    _run(config.store_path, "b1", ENGINEER_B)
    _run(config.store_path, "b2", ENGINEER_B)
    _reserved_yesterday(config.store_path, "b1", 0.05)
    _reserved_yesterday(config.store_path, "b2", 0.05)
    assert _reserve(config, "b1", 0.005, policy)[0]
    assert budget.reserve_batch_daily(config, [("b2", 1, "llm:plan:1", 0.02)], 2.0, ENGINEER_B, 0.30)[0]
    assert _reservation_day(config.store_path, "b1") == (pytest.approx(0.005), today), "chuyển sang hôm nay, đủ số"
    assert _reservation_day(config.store_path, "b2") == (pytest.approx(0.02), today)
    assert quota.spent_today(config.store_path, ENGINEER_B) == pytest.approx(0.025)
    assert _reserve(config, "b1", 0.005, policy)[0], "khoản đã giữ hôm nay vẫn dùng lại được"
    assert quota.spent_today(config.store_path, ENGINEER_B) == pytest.approx(0.025)


def test_previous_day_reservation_is_rebooked_against_todays_global_cap(tmp_path):
    config, policy = _settings(tmp_path), load_policy()
    _run(config.store_path, "b1", ENGINEER_B)
    _run(config.store_path, "b2", ENGINEER_B)
    _reserved_yesterday(config.store_path, "b1", 0.05)
    _reserved_yesterday(config.store_path, "b2", 0.05)
    _reserved_yesterday(config.store_path, "legacy-old", 0.05, category="scan")
    assert budget.reserve_daily(config, "legacy-run", 1, "scan", 1.995, 2.0)[0]  # trần chung còn 0.005

    assert _reserve(config, "b1", 0.01, policy) == (False, pytest.approx(1.995), None), "chặn bởi trần chung"
    assert budget.reserve_batch_daily(config, [("b2", 1, "llm:plan:1", 0.01)], 2.0, ENGINEER_B, 0.30)[0] is False
    assert budget.reserve_daily(config, "legacy-old", 1, "scan", 0.01, 2.0)[0] is False
    assert budget.spent_today(config) == pytest.approx(1.995)


def _regression_runs(store_path: str) -> list[str]:
    with _database(store_path) as conn:
        query = "SELECT run_id FROM run_reports WHERE json_extract(report, '$.trigger') = 'regression'"
        return [row[0] for row in conn.execute(query)]


def test_scheduled_regression_fix_claims_the_pipeline_owners_run_slot(tmp_path):
    from src.monitor import alerts, day2
    from tests.test_agents.test_graph import FakeLLM, deps

    config = _settings(tmp_path)
    parent = _run(config.store_path, "a-dep", "minh")
    evidence = {"model": "orders_model", "rules": [{"rule": "runtime_regression", "detail": "chậm"}]}
    launch = day2.launcher(
        deps(FakeLLM(), policy=_with_per_user(load_policy(), max_runs_per_day=2)), config.store_path, lambda job: None
    )

    started = launch(parent, evidence)

    assert _slots(config.store_path) == [started.run_id], "run sửa dùng đúng lượt đã giữ cho chủ pipeline"
    assert quota.usage(config.store_path, auth.USERS["minh"], load_policy().budgets)["runs"]["used"] == 2
    with pytest.raises(quota.RunQuotaExceeded, match="Chủ pipeline đã dùng hết 2 run hôm nay"):
        launch(parent, evidence)
    assert _regression_runs(config.store_path) == [started.run_id], "hết lượt thì không tạo run sửa"
    (alert,) = alerts.list_alerts(config.store_path, "orders_model")
    assert alert["kind"] == "needs_human" and alert["owner"] == "minh" and "hết hạn mức run" in alert["message"]


def _degraded(monkeypatch, parents: dict[str, RunReport]) -> None:
    """Các pipeline `parents` (model → bản đang deploy) đã deploy và đều chậm đi: day2.check thật sẽ tạo run sửa."""
    from src.monitor import day2
    from src.monitor.detectors import Finding

    slow = Finding("runtime_regression", 120.0, 60.0, 40.0, "chậm gấp 3 đường cơ sở")
    monkeypatch.setattr(day2.runs, "deployed_models", lambda deploy_dir: list(parents))
    monkeypatch.setattr(day2.runs, "deployed_report", lambda store_path, model: parents.get(model))
    monkeypatch.setattr(day2.runs, "series", lambda store_path, model: {"baseline": None, "runs": []})
    monkeypatch.setattr(day2.detectors, "detect", lambda series, objectives, now: [slow])


def test_scheduled_check_skips_an_exhausted_owner_and_still_repairs_the_next_pipeline(tmp_path, monkeypatch):
    from src.monitor import alerts, day2
    from tests.test_agents.test_graph import FakeLLM, deps

    config = _settings(tmp_path)
    policy = _with_per_user(load_policy(), max_runs_per_day=2)
    domain = next(iter(policy.objectives))
    parents = {
        "a_model": _run(config.store_path, "a-dep", "minh", domain=domain),
        "b_model": _run(config.store_path, "b-dep", ENGINEER_B, domain=domain),
    }
    _run(config.store_path, "a-extra", "minh")  # minh đã dùng đủ 2 run hôm nay, eng.b mới dùng 1
    _degraded(monkeypatch, parents)
    submitted = []
    launch = day2.launcher(deps(FakeLLM(), policy=policy), config.store_path, submitted.append)

    # Như vòng theo lịch (scripts/dagster_local.py collect): xét mọi pipeline, chủ đầu hết lượt không làm dừng cả lượt.
    actions = day2.check(config, policy, launch)

    assert [(a["model"], a["action"]) for a in actions] == [
        ("a_model", "quota_exhausted"),
        ("b_model", "fix_started"),
    ]
    assert actions[0]["detail"].startswith("Chủ pipeline đã dùng hết 2 run hôm nay")
    (fix,) = _regression_runs(config.store_path)
    assert fix == actions[1]["run_id"] and len(submitted) == 1, "run sửa của pipeline thứ hai vẫn chạy nền"
    (alert,) = alerts.list_alerts(config.store_path, "a_model")
    assert alert["kind"] == "needs_human" and alert["owner"] == "minh" and "hết hạn mức run" in alert["message"]
    assert quota.usage(config.store_path, auth.USERS["minh"], policy.budgets)["runs"]["used"] == 2


@pytest.mark.asyncio
async def test_monitor_check_returns_429_when_the_pipeline_owner_has_no_run_left(quota_env, monkeypatch):
    from src.monitor import alerts

    domain = next(iter(load_policy().objectives))
    parent = RunReport.model_validate_json(
        (FIXTURES_DIR / "ready_for_review.json").read_text(encoding="utf-8")
    ).model_copy(update={"run_id": "a1", "domain": domain})
    _degraded(monkeypatch, {"orders_model": parent})
    monkeypatch.setattr(monitor, "_own_pipelines", lambda user, config: {"orders_model"})
    app.dependency_overrides[monitor.monitor_deps] = lambda: app.dependency_overrides[routes.agent_deps]()
    try:
        await _login(quota_env.client, "engineer", "Engineer@123")
        response = await quota_env.client.post("/api/v1/monitor/check")
    finally:
        app.dependency_overrides.pop(monitor.monitor_deps, None)

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["detail"].startswith("Chủ pipeline đã dùng hết 1 run hôm nay")
    assert [(a["model"], a["action"]) for a in body["actions"]] == [("orders_model", "quota_exhausted")]
    assert _regression_runs(quota_env.config.store_path) == [] and _slots(quota_env.config.store_path) == []
    assert [a["kind"] for a in alerts.list_alerts(quota_env.config.store_path, "orders_model")] == ["needs_human"]


@pytest.mark.asyncio
async def test_failed_batch_reservation_returns_the_claimed_run_slots(open_env, monkeypatch):
    import sqlite3

    from tests.test_agents.test_graph import FakeLLM, deps

    store_path, minh = open_env.config.store_path, auth.USERS["minh"]
    app.dependency_overrides[routes.agent_deps] = lambda: deps(FakeLLM(), policy=open_env.policy)

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(budget, "reserve_batch_daily", locked)
    await _login(open_env.client, "engineer", "Engineer@123")
    before = quota.usage(store_path, minh, open_env.policy.budgets)

    try:
        response = await open_env.client.post(
            "/api/v1/agent/runs/batch", json={"requests": [{"request": "A"}, {"request": "B"}]}
        )
        assert response.status_code == 500
    except sqlite3.OperationalError:
        pass

    assert _slots(store_path) == []
    assert quota.usage(store_path, minh, open_env.policy.budgets) == before
    assert before["runs"]["used"] == 0
