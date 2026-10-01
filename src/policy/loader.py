"""Nạp và xác thực policy/*.yml.

Không cache: mỗi lần gọi đều đọc lại file, nên sửa YAML là hành vi đổi theo mà không cần build lại (NFR-04).
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from src.policy.models import Policy

POLICY_DIR = Path(__file__).resolve().parents[2] / "policy"
POLICY_FILES = ("domains", "budgets", "objectives")


class PolicyError(ValueError):
    """Chính sách không nạp được; thông điệp nêu rõ file và lỗi."""


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"thiếu file chính sách {path.name}") from exc
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path.name}: YAML không hợp lệ: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path.name}: nội dung gốc phải là mapping")
    return data


def _format(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '<gốc>'}: {e['msg']}" for e in error.errors())


def load_policy(policy_dir: Path = POLICY_DIR) -> Policy:
    raw = {name: _read_yaml(Path(policy_dir) / f"{name}.yml") for name in POLICY_FILES}
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(f"chính sách không hợp lệ: {_format(exc)}") from exc
