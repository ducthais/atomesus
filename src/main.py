import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.catalog import router as catalog_router
from src.api.monitor import pipelines_router
from src.api.monitor import router as monitor_router
from src.api.orchestration import router as orchestration_router
from src.api.policy import router as policy_router
from src.api.routes import router
from src.api.webhooks import router as webhooks_router
from src.chat import router as chat_router
from src.config import get_settings
from src.pipeline.api import router as pipeline_router
from src.pipeline.connections import router as connections_router
from src.pipeline.settings import PipelineSettings
from src.policy.loader import load_policy
from src.run_report import router as run_reports_router
from src.run_report.store import abandon_unfinished


async def sweep_unfinished(store_path: str, stale_seconds: float) -> None:
    """Đóng run mồ côi sau khi nó không cập nhật quá lâu; an toàn khi nhiều worker dùng chung store."""
    while True:
        await asyncio.sleep(stale_seconds)
        stale_before = datetime.now(UTC) - timedelta(seconds=stale_seconds)
        try:
            abandoned = abandon_unfinished(store_path, stale_before=stale_before)
        except Exception as exc:  # store bận/khóa: bỏ lượt này, vòng quét vẫn phải sống tới lượt sau
            print(f"Quét run mồ côi lỗi: {type(exc).__name__}: {exc}")
            continue
        if abandoned:
            print(f"Đánh dấu lỗi {len(abandoned)} run không còn tiến triển")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    # Agent chạy nền trong tiến trình này; run dở dang từ lần chạy trước không còn ai hoàn tất.
    pipeline = PipelineSettings()
    pipeline.validate_connection_key(settings.app_env)
    stale_seconds = load_policy().budgets.per_run.phase_timeout_sec * 5
    stale_before = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    if abandoned := abandon_unfinished(pipeline.store_path, stale_before=stale_before):
        print(f"Đánh dấu lỗi {len(abandoned)} run bị bỏ dở khi backend tắt")
    sweeper = asyncio.create_task(sweep_unfinished(pipeline.store_path, stale_seconds))
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        print("Shutting down...")


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(pipeline_router, prefix="/api/v1")
app.include_router(connections_router, prefix="/api/v1")
app.include_router(run_reports_router, prefix="/api/v1")
app.include_router(policy_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(orchestration_router, prefix="/api/v1")
app.include_router(monitor_router, prefix="/api/v1")
app.include_router(pipelines_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.app_env}
