"""Sinh src/spec/spec.schema.json từ model Spec. Chạy lại sau mỗi lần sửa src/spec/models.py.

.venv/Scripts/python.exe scripts/export_spec_schema.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.spec import SCHEMA_PATH, Spec  # noqa: E402


def render_schema() -> str:
    schema = Spec.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$comment"] = "Sinh tự động từ src/spec/models.py. Ràng buộc chéo giữa các trường chỉ có ở model."
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.write_text(render_schema(), encoding="utf-8")
    print(f"đã ghi {SCHEMA_PATH}")
