"""Ghép lựa chọn nguồn của người dùng vào chính sách YAML, chỉ đọc metadata trong store."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from src.policy.loader import PolicyError, _format, load_policy
from src.policy.models import Policy

if TYPE_CHECKING:
    from src.pipeline.settings import PipelineSettings


def read_user_selections(store_path: str, owner_id: str | None = None) -> list[dict]:
    """Không tạo store hay chạy migration; store cũ chưa có lựa chọn thì chỉ dùng YAML."""
    path = Path(store_path).resolve()
    if not path.exists():
        return []
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"connections", "connection_selections"} <= tables:
                return []
            has_email = "users" in tables and any(row[1] == "email" for row in conn.execute("PRAGMA table_info(users)"))
            email = "u.email" if has_email else "NULL"
            query = (
                f"SELECT c.id, c.owner_id, c.name, s.schema_name, s.tables_json, {email}"
                " FROM connections c JOIN connection_selections s ON s.connection_id = c.id"
            )
            if has_email:
                query += " LEFT JOIN users u ON u.id = c.owner_id"
            params = ()
            if owner_id is not None:
                query += " WHERE c.owner_id = ?"
                params = (owner_id,)
            rows = conn.execute(query + " ORDER BY c.id", params).fetchall()
        return [
            {
                "id": row[0],
                "owner_id": row[1],
                "label": row[2],
                "schema": row[3],
                "tables": json.loads(row[4]),
                "email": row[5],
            }
            for row in rows
        ]
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise PolicyError("Không đọc được lựa chọn nguồn đã lưu trong store.") from exc


def effective_policy(config: "PipelineSettings", owner_id: str | None = None) -> Policy:
    """None dành cho hệ thống/Reviewer; Engineer chỉ nhận domain YAML và domain của mình."""
    policy = load_policy()
    selections = read_user_selections(config.store_path, owner_id)
    if not selections:
        return policy
    template = policy.objectives.get("_user_default")
    if template is None:
        raise PolicyError("objectives.yml thiếu mẫu _user_default cho domain người dùng.")
    raw = policy.model_dump()
    domains = raw["domains"]["domains"]
    for selection in selections:
        connection_id = selection["id"]
        name, source_ref = f"u_{connection_id[:8]}", f"c_{connection_id[:8]}"
        if name in domains or any(source_ref in domain["sources"] for domain in domains.values()):
            raise PolicyError(f"Domain hoặc nguồn người dùng '{name}' bị trùng tên trong chính sách.")
        if not isinstance(selection["tables"], list):
            raise PolicyError("Lựa chọn nguồn đã lưu phải có danh sách bảng.")
        domains[name] = {
            "label": selection["label"],
            "owner_id": selection["owner_id"],
            "datasets": [selection["schema"]],
            "steward": selection["email"] or f"{selection['owner_id']}@users.local",
            "currency": policy.domains.user_defaults.currency.model_dump(),
            "sources": {
                source_ref: {
                    "kind": "postgres",
                    "connection_ref": f"conn:{connection_id}",
                    "schema": selection["schema"],
                    "tables": selection["tables"],
                }
            },
        }
        raw["objectives"][name] = template.model_dump()
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError(f"Chính sách hiệu lực không hợp lệ: {_format(exc)}") from exc
