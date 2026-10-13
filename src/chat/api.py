"""API chat bản Sprint 1 (SPEC §14.5, §15, FR-27).

- `POST /chat` không có `run_id`: `new_request`, lập kế hoạch rồi dừng chờ xác nhận bằng nút (P2), chỉ Engineer.
- `POST /chat` có `run_id`: `ask`, trả lời từ RunReport, không đổi trạng thái run, trần `max_chat_calls`;
  hoặc `revise` (câu mệnh lệnh đổi pipeline, chỉ Engineer sở hữu run): tạo version mới, agent viết lại Spec ở nền.
- `GET /runs/{run_id}/messages`: lịch sử chat của run.
Chat không duyệt, không deploy: cổng người chỉ qua nút bấm.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from src.agents.nodes.common import AgentDeps
from src.agents.service import RevisionNotAllowedError, RunChangedError, plan_agent, start_revision
from src.api.routes import AgentRunRequest, _checked_sources, agent_deps
from src.chat import answers, store
from src.chat.models import ChatMessage, ChatRequest, ChatResponse
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.run_report.models import RunReport
from src.run_report.store import get_run_report

router = APIRouter(tags=["chat"])


def _existing_run(store_path: str, run_id: str) -> RunReport:
    report = get_run_report(store_path, run_id)
    if report is None:
        raise HTTPException(404, f"Không tìm thấy run {run_id}")
    return report


@router.post("/chat", response_model=ChatResponse)
def post_chat(
    body: ChatRequest,
    background: BackgroundTasks,
    user: dict = Depends(current_user),
    deps: AgentDeps = Depends(agent_deps),
    config: PipelineSettings = Depends(settings),
) -> ChatResponse:
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Tin nhắn không được để trống.")
    if body.run_id is not None and body.sources is not None:
        raise HTTPException(422, "Chỉ chọn nguồn khi tạo pipeline mới từ chat.")

    if body.run_id is None:
        if user["role"] != "Engineer":
            raise HTTPException(403, "Chỉ Engineer được tạo pipeline từ chat.")
        sources = _checked_sources(AgentRunRequest(request=message, sources=body.sources), user, deps)
        if sources is None:
            report = plan_agent(message, user["id"], deps, config.store_path)
        else:
            report = plan_agent(message, user["id"], deps, config.store_path, sources=sources)
        intent, reply = "new_request", answers.created_reply(report)
    elif answers.intent(message) == "revise":
        report = _existing_run(config.store_path, body.run_id)
        if user["role"] != "Engineer":
            raise HTTPException(403, "Reviewer không sửa pipeline qua chat; dùng nút Yêu cầu sửa trên run.")
        if report.created_by != user["id"]:
            raise HTTPException(403, "Chỉ Engineer tạo run được sửa run đó.")
        try:
            report, job = start_revision(report, message, user["id"], deps, config.store_path)
        except (RevisionNotAllowedError, RunChangedError) as exc:
            raise HTTPException(409, str(exc)) from exc
        background.add_task(job)
        intent, reply = "revise", answers.revised_reply(report, message)
    else:
        report = _existing_run(config.store_path, body.run_id)
        limit = deps.policy.budgets.per_run.max_chat_calls
        if store.count_questions(config.store_path, report.run_id) >= limit:
            raise HTTPException(429, f"Run đã dùng hết {limit} câu hỏi (budgets.yml: per_run.max_chat_calls).")
        intent, reply = "ask", answers.answer(report, message)

    store.save_exchange(config.store_path, report.run_id, user["id"], intent, message, reply)
    return ChatResponse(intent=intent, reply=reply, run_id=report.run_id, report=report)


@router.get("/runs/{run_id}/messages", response_model=list[ChatMessage], dependencies=[Depends(current_user)])
def get_messages(run_id: str, config: PipelineSettings = Depends(settings)) -> list[ChatMessage]:
    _existing_run(config.store_path, run_id)
    return store.list_messages(config.store_path, run_id)
