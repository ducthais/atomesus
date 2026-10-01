"""Module Spec — bản thiết kế pipeline và kiểm tra theo chính sách (Sprint 0 - Role C)."""

from pathlib import Path

from src.spec.models import Spec
from src.spec.policy_check import policy_violations

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "spec.schema.json"
SPEC_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "specs"

__all__ = ["Spec", "policy_violations", "SCHEMA_PATH", "SPEC_FIXTURES_DIR"]
