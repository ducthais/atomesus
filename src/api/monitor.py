from collections.abc import Callable, Iterable
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.agents.manual_optimize import start_manual_optimize
from src.agents.nodes.common import AgentDeps
from src.api.routes import agent_deps
from src.monitor import alerts, day2, runs
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import PolicyError, effective_policy
from src.run_report import RunReport
from src.run_report.access import SYSTEM, model_visible, owner_of, run_owner, scope

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(current_user)])
pipelines_router = APIRouter(prefix="/pipelines", tags=["pipelines"])
ALERT_LIMIT = 100
ALERT_SCAN = 1000  # đọc rộng rồi lọc theo quyền, để cảnh báo của mình không bị cảnh báo người khác đẩy khỏi trang


def monitor_deps(config: PipelineSettings = Depends(settings), deps: AgentDeps = Depends(agent_deps)) -> AgentDeps:
    """Chính sách đủ mọi domain để đọc được Spec của pipeline đã deploy; quyền xem lọc riêng bằng `visible_models`."""
    try:
        return replace(deps, policy=effective_policy(config))
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc


def visible_models(user: dict, store_path: str, models: Iterable[str]) -> set[str]:
    """Pipeline người dùng được thấy, xét theo chủ của tên model (xem src.run_report.access.model_owner)."""
    return {model for model in models if model_visible(user, store_path, model)}


@pipelines_router.post("/{model}/optimize", status_code=202, response_model=RunReport)
def optimize_pipeline(
    model: str,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
    deps: AgentDeps = Depends(monitor_deps),
) -> RunReport:
    """Engineer yêu cầu tối ưu pipeline đã deploy (UC-3): chạy nền lại pipeline và optimizer P13, không gọi LLM."""
    # Xét quyền thấy trước vai: pipeline không được thấy là 404 với mọi vai, không lộ pipeline đó có tồn tại.
    parent = runs.deployed_report(config.store_path, model)
    if parent is None or not model_visible(user, config.store_path, model):
        raise HTTPException(404, f"Pipeline '{model}' chưa được deploy.")
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được yêu cầu tối ưu pipeline.")
    started, job = start_manual_optimize(parent, user["id"], deps, config.store_path)
    background.add_task(job)
    return started


def _engineer(user: dict) -> None:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được chạy monitor hoặc xử lý cảnh báo.")


def _own_pipelines(user: dict, config: PipelineSettings) -> set[str]:
    """Pipeline đã deploy mà người gọi được thấy: bấm tay chỉ xử lý những pipeline này. Lịch monitor của hệ thống
    (scripts/dagster_local.py) vẫn xử lý mọi pipeline."""
    return visible_models(user, config.store_path, runs.deployed_models(config.deploy_dir))


@router.post("/collect")
def collect_runs(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Thu số đo các lần chạy theo lịch đã xong mà chưa ghi (đọc Dagster, dbt, warehouse, nguồn), chỉ cho pipeline
    người gọi được thấy. Chỉ đọc."""
    _engineer(user)
    try:
        written = runs.collect(config, effective_policy(config), models=_own_pipelines(user, config))
    except runs.MonitorError as exc:
        raise HTTPException(503, str(exc)) from exc
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"collected": len(written), "runs": written}


@router.get("/pipelines")
def list_pipelines(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> list[dict]:
    """Pipeline đã deploy mà người dùng được thấy: lần chạy gần nhất kể từ lần deploy hiện tại, đường cơ sở và các
    luật suy giảm đang kích hoạt (chỉ đọc: không cảnh báo, không tạo run sửa; việc đó là của POST /monitor/check)."""
    try:
        policy = effective_policy(config)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    found = day2.overview(config, policy)
    visible = visible_models(user, config.store_path, {row["model"] for row in found})
    return [row for row in found if row["model"] in visible]


@router.get("/pipelines/{model}")
def pipeline_series(
    model: str, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> dict:
    """Chuỗi số đo của một pipeline: đường cơ sở (lúc chạy thử) và từng lần chạy theo lịch."""
    if not visible_models(user, config.store_path, {model}):
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
    """Chạy 4 luật suy giảm cho các pipeline đã deploy mà người gọi được thấy (Day 2). Run sửa chạy nền, thuộc chủ
    pipeline; pipeline của người khác không bị cảnh báo hay tạo run sửa."""
    _engineer(user)
    launch = day2.launcher(deps, config.store_path, background.add_task)
    return {"actions": day2.check(config, deps.policy, launch, models=_own_pipelines(user, config))}


def _alert_filter(user: dict, store_path: str) -> Callable[[dict], bool]:
    """Cảnh báo gắn với run thì xét quyền trên chính run đó, không thì theo chủ pipeline đã lưu lúc cảnh báo. Tên
    model chỉ dùng cho cảnh báo cũ ghi chủ là `system` (trước khi lần về chủ run gốc), vì hai người có thể trùng tên."""
    access = scope(user, store_path)

    def visible(alert: dict) -> bool:
        if access.owners is None:
            return True
        if alert["run_id"]:
            return access.allows(run_owner(store_path, alert["run_id"]))
        if alert["owner"] == SYSTEM:
            return bool(visible_models(user, store_path, {alert["model"]}))
        return access.allows(owner_of(store_path, alert["owner"]))

    return visible


@router.get("/alerts")
def list_alerts(
    model: str | None = None,
    include_resolved: bool = False,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> list[dict]:
    """Cảnh báo Day 2: nguồn ngừng cấp, pipeline gắn cờ, run sửa cần người vào sửa. Mới nhất trước; chỉ cảnh báo
    người dùng được thấy."""
    found = alerts.list_alerts(config.store_path, model, open_only=not include_resolved, limit=ALERT_SCAN)
    return list(filter(_alert_filter(user, config.store_path), found))[:ALERT_LIMIT]


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: int, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)
) -> dict:
    """Đánh dấu cảnh báo đã xử lý. Xét quyền thấy trước vai: cảnh báo không được thấy là 404 với mọi vai."""
    alert = alerts.get_alert(config.store_path, alert_id)
    if alert is None or not _alert_filter(user, config.store_path)(alert):
        raise HTTPException(404, f"Không có cảnh báo {alert_id}")
    _engineer(user)
    found = alerts.resolve_alert(config.store_path, alert_id)
    if found is None:
        raise HTTPException(404, f"Không có cảnh báo {alert_id}")
    return found
