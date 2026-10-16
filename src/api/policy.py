from fastapi import APIRouter, Depends, HTTPException

from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy.loader import POLICY_DIR, POLICY_FILES, PolicyError
from src.run_report.access import policy_for

router = APIRouter(prefix="/policy", tags=["policy"], dependencies=[Depends(current_user)])


@router.get("")
def read_policy(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Các trần UI cần hiển thị, đọc lại mỗi lần (NFR-04); nội dung 3 file chính sách (chỉ đọc) chỉ trả cho Admin.

    File YAML có domain ẩn, email steward, tên kết nối nội bộ và đường dẫn máy chủ, nên vai khác nhận `files` rỗng
    (giữ khóa để client cũ không hỏng)."""
    try:
        policy = policy_for(user, config)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    per_run = policy.budgets.per_run
    files = (
        {f"{name}.yml": (POLICY_DIR / f"{name}.yml").read_text(encoding="utf-8") for name in POLICY_FILES}
        if user["role"] == "Admin"
        else {}
    )
    return {
        "files": files,
        "user_domains": [
            {"name": name, "label": domain.label, "schema": decl.schema_, "tables": decl.tables}
            for name, domain in policy.domains.domains.items()
            if domain.owner_id is not None
            for decl in domain.sources.values()
        ],
        "limits": {
            "max_llm_calls": per_run.max_llm_calls,
            "max_spec_attempts": per_run.max_spec_attempts,
            "max_sql_attempts": per_run.max_sql_attempts,
            "max_chat_calls": per_run.max_chat_calls,
            "max_usd_per_day": policy.budgets.per_day.max_usd,
        },
    }
