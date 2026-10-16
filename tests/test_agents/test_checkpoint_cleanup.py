"""MemorySaver thật: checkpoint của một run bị xóa ngay khi lượt chạy kết thúc (src/agents/service.py: _invoke).

Mỗi lượt (lập kế hoạch, xác nhận, chạy trọn) là một thread `<run_id>:<entry>`; kết thúc theo cách nào (xong, cần người,
lỗi ngoài dự kiến) cũng không để lại trạng thái agent trong bộ nhớ tiến trình. Thread không thuộc run đó vẫn giữ nguyên.
"""

from dataclasses import replace

from langgraph.checkpoint.memory import MemorySaver

from src.agents.graph import build_graph
from src.agents.service import confirm_plan, plan_agent, run_agent
from tests.test_agents.test_graph import PLAN, SPEC, FakeLLM, deps


def _threads(saver: MemorySaver) -> set[str]:
    return {item.config["configurable"]["thread_id"] for item in saver.list(None)}


def _with(saver: MemorySaver, llm: FakeLLM, **changes):
    return replace(deps(llm), checkpointer=saver, **changes)


def test_full_run_leaves_no_checkpoint(tmp_path):
    saver = MemorySaver()

    report = run_agent("Doanh thu theo ngày", "minh", _with(saver, FakeLLM(PLAN, SPEC)), str(tmp_path / "r.db"))

    assert report.status == "READY_FOR_REVIEW", report.error_trace
    assert _threads(saver) == set() and not saver.storage


def test_plan_then_confirm_each_release_their_own_thread(tmp_path):
    saver, store = MemorySaver(), str(tmp_path / "r.db")
    agent = _with(saver, FakeLLM(PLAN, SPEC))

    planned = plan_agent("Doanh thu theo ngày", "minh", agent, store)

    assert (planned.status, planned.stage_reached) == ("DRAFT", "confirm")
    assert _threads(saver) == set(), "chờ Engineer xác nhận không giữ trạng thái agent trong bộ nhớ"

    done = confirm_plan(planned, "minh", agent, store)

    assert done.status == "READY_FOR_REVIEW", done.error_trace
    assert _threads(saver) == set() and not saver.storage


def test_needs_human_and_unexpected_errors_also_release_the_thread(tmp_path):
    saver = MemorySaver()

    needs_human = run_agent(
        "Doanh thu theo ngày",
        "minh",
        _with(saver, FakeLLM(PLAN)),
        str(tmp_path / "a.db"),
        sources=[{"source_ref": "missing_pg", "tables": []}],
    )
    assert needs_human.status == "NEEDS_HUMAN"
    assert _threads(saver) == set()

    def broken_catalog(policy, settings):
        raise RuntimeError("catalog hỏng")

    failed = run_agent(
        "Doanh thu theo ngày", "minh", _with(saver, FakeLLM(PLAN), catalog=broken_catalog), str(tmp_path / "b.db")
    )

    assert failed.status in {"INTERNAL_ERROR", "INFRA_ERROR", "NEEDS_HUMAN"}, failed.status
    assert _threads(saver) == set() and not saver.storage


def test_other_threads_are_kept(tmp_path):
    saver = MemorySaver()
    other = {"configurable": {"thread_id": "other-run:planner"}}
    build_graph(deps(FakeLLM(PLAN, SPEC)), checkpointer=saver).invoke(
        {"run_id": "other-run", "version": 1, "user_request": "Doanh thu theo ngày", "llm_calls": 0, "error_trace": []},
        config=other,
    )

    run_agent("Doanh thu theo ngày", "minh", _with(saver, FakeLLM(PLAN, SPEC)), str(tmp_path / "r.db"))

    assert _threads(saver) == {"other-run:planner"}, "chỉ xóa thread của chính run vừa chạy"
