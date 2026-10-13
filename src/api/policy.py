from fastapi import APIRouter, Depends, HTTPException

from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import effective_policy
from src.policy.loader import POLICY_DIR, POLICY_FILES, PolicyError

router = APIRouter(prefix="/policy", tags=["policy"], dependencies=[Depends(current_user)])


@router.get("")
def read_policy(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Nội dung 3 file chính sách (chỉ đọc) và các trần UI cần hiển thị, đọc lại mỗi lần (NFR-04)."""
    try:
        policy = effective_policy(config, owner_id=user["id"] if user["role"] == "Engineer" else None)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    per_run = policy.budgets.per_run
    return {
        "files": {f"{name}.yml": (POLICY_DIR / f"{name}.yml").read_text(encoding="utf-8") for name in POLICY_FILES},
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
