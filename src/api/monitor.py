from collections.abc import Callable
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.agents.manual_optimize import start_manual_optimize
from src.agents.nodes.common import AgentDeps
from src.api.routes import agent_deps
from src.monitor import alerts, day2, runs
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import PolicyError, effective_policy
from src.run_report import RunReport, get_run_report
from src.run_report.access import can_view_run, run_owner, scope

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(current_user)])
pipelines_router = APIRouter(prefix="/pipelines", tags=["pipelines"])
ALERT_LIMIT = 100
ALERT_SCAN = 1000  # đọc rộng rồi lọc theo quyền, để cảnh báo của mình không bị cảnh báo người khác đẩy khỏi trang


def monitor_deps(config: PipelineSettings = Depends(settings), deps: AgentDeps = Depends(agent_deps)) -> AgentDeps:
    """Chính sách đủ mọi domain để đọc được Spec của pipeline đã deploy; quyền xem lọc riêng bằng `_pipelines_of`."""
    try:
        return replace(deps, policy=effective_policy(config))
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc


def _pipelines_of(user: dict, store_path: str) -> Callable[[RunReport | None], bool]:
    """Pipeline người dùng được thấy, xét theo run đang deploy (cùng luật với run, xem src.run_report.access)."""
    access = scope(user, store_path)
    return lambda parent: access.allows(run_owner(store_path, parent) if parent else None)


def _visible_models(user: dict, store_path: str, models: set[str]) -> set[str]:
    allowed = _pipelines_of(user, store_path)
    return {model for model in models if allowed(runs.deployed_report(store_path, model))}


@pipelines_router.post("/{model}/optimize", status_code=202, response_model=RunReport)
def optimize_pipeline(
    model: str,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
    deps: AgentDeps = Depends(monitor_deps),
) -> RunReport:
    """Engineer yêu cầu tối ưu pipeline đã deploy (UC-3): chạy nền lại pipeline và optimizer P13, không gọi LLM."""
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được yêu cầu tối ưu pipeline.")
    parent = runs.deployed_report(config.store_path, model)
    if parent is None or not _pipelines_of(user, config.store_path)(parent):
        raise HTTPException(404, f"Pipeline '{model}' chưa được deploy.")
    started, job = start_manual_optimize(parent, user["id"], deps, config.store_path)
    background.add_task(job)
    return started


def _engineer(user: dict) -> None:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được chạy monitor hoặc xử lý cảnh báo.")


@router.post("/collect")
def collect_runs(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Thu số đo các lần chạy theo lịch đã xong mà chưa ghi (đọc Dagster, dbt, warehouse, nguồn). Chỉ đọc.

    Thu cho mọi pipeline (số đo là của hệ thống), nhưng chỉ trả về số đo của pipeline người gọi được thấy.
    """
    _engineer(user)
    try:
        written = runs.collect(config, effective_policy(config))
    except runs.MonitorError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    visible = _visible_models(user, config.store_path, {row["model"] for row in written})
    mine = [row for row in written if row["model"] in visible]
    return {"collected": len(mine), "runs": mine}


@router.get("/pipelines")
def list_pipelines(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> list[dict]:
    """Pipeline đã deploy mà người dùng được thấy: lần chạy gần nhất kể từ lần deploy hiện tại, đường cơ sở và các
    luật suy giảm đang kích hoạt (chỉ đọc: không cảnh báo, không tạo run sửa; việc đó là của POST /monitor/check)."""
    try:
        policy = effective_policy(config)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    found = day2.overview(config, policy)
    visible = _visible_models(user, config.store_path, {row["model"] for row in found})
    return [row for row in found if row["model"] in visible]


@router.get("/pipelines/{model}")
def pipeline_series(
    model: str, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> dict:
    """Chuỗi số đo của một pipeline: đường cơ sở (lúc chạy thử) và từng lần chạy theo lịch."""
    if not _visible_models(user, config.store_path, {model}):
        raise HTTPException(404, f"Pipeline '{model}' chưa deploy hoặc chưa có số đo")
    found = runs.series(config.store_path, model)
    if found["baseline"] is None and not found["runs"]:
        raise HTTPException(404, f"Pipeline '{model}' chưa deploy hoặc chưa có số đo")
    return found


@router.post("/check")
def check_pipelines(
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
    deps: AgentDeps = Depends(monitor_deps),
) -> dict:
    """Chạy 4 luật suy giảm cho mọi pipeline đã deploy (Day 2, việc của hệ thống như lịch monitor). Run sửa chạy nền,
    thuộc chủ pipeline; chỉ trả việc đã làm trên pipeline người gọi được thấy."""
    _engineer(user)
    actions = day2.check(config, deps.policy, day2.launcher(deps, config.store_path, background.add_task))
    visible = _visible_models(user, config.store_path, {action["model"] for action in actions})
    return {"actions": [action for action in actions if action["model"] in visible]}


def _alert_visible(alert: dict, user: dict, store_path: str, models: set[str]) -> bool:
    """Cảnh báo thuộc pipeline người dùng thấy, hoặc gắn với run người dùng thấy."""
    if alert["model"] in models:
        return True
    report = get_run_report(store_path, alert["run_id"]) if alert["run_id"] else None
    return report is not None and can_view_run(user, report, store_path)


@router.get("/alerts")
def list_alerts(
    model: str | None = None,
    include_resolved: bool = False,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> list[dict]:
    """Cảnh báo Day 2: nguồn ngừng cấp, pipeline gắn cờ, run sửa cần người vào sửa. Mới nhất trước; chỉ cảnh báo
    của pipeline người dùng được thấy."""
    found = alerts.list_alerts(config.store_path, model, open_only=not include_resolved, limit=ALERT_SCAN)
    models = _visible_models(user, config.store_path, {alert["model"] for alert in found})
    return [alert for alert in found if _alert_visible(alert, user, config.store_path, models)][:ALERT_LIMIT]


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: int, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> dict:
    """Đánh dấu cảnh báo đã xử lý."""
    _engineer(user)
    alert = alerts.get_alert(config.store_path, alert_id)
    if alert is None or not _alert_visible(
        alert, user, config.store_path, _visible_models(user, config.store_path, {alert["model"]})
    ):
        raise HTTPException(404, f"Không có cảnh báo {alert_id}")
    found = alerts.resolve_alert(config.store_path, alert_id)
    if found is None:
        raise HTTPException(404, f"Không có cảnh báo {alert_id}")
    return found
