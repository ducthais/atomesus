from fastapi import APIRouter, Depends, HTTPException

from src.pipeline.auth import current_user
from src.policy.loader import POLICY_DIR, POLICY_FILES, PolicyError, load_policy

router = APIRouter(prefix="/policy", tags=["policy"], dependencies=[Depends(current_user)])


@router.get("")
def read_policy() -> dict:
    """Nội dung 3 file chính sách (chỉ đọc) và các trần UI cần hiển thị, đọc lại mỗi lần (NFR-04)."""
    try:
        policy = load_policy()
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc
    per_run = policy.budgets.per_run
    return {
        "files": {f"{name}.yml": (POLICY_DIR / f"{name}.yml").read_text(encoding="utf-8") for name in POLICY_FILES},
        "limits": {
            "max_llm_calls": per_run.max_llm_calls,
            "max_spec_attempts": per_run.max_spec_attempts,
            "max_sql_attempts": per_run.max_sql_attempts,
            "max_usd_per_day": policy.budgets.per_day.max_usd,
        },
    }
