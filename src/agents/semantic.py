"""Cổng `semantic` (P3, SPEC §9.1, FR-05): chặn từ có hai nghĩa ở hai domain trước khi tốn LLM sinh Spec.

`entity_scan`, bản MVP dùng từ khóa (không gọi LLM):
1. Thực thể được nhắc trong yêu cầu: tên thực thể hoặc một `aliases` của nó trong `domains.yml`.
2. Thực thể đó định nghĩa ở từ hai domain trở lên và chưa nằm trong `conformed_dimensions` → xung đột:
   `BLOCKED_SEMANTIC_CONFLICT`, thông điệp nêu đủ các định nghĩa và steward phải quyết định.
3. Ngoại lệ: người dùng đã chọn nguồn (`source_scope`) và mọi nguồn đã chọn thuộc đúng một trong các domain đó thì nghĩa
   đã rõ; cổng đạt và ghi lại định nghĩa được dùng làm bằng chứng.
"""

import re

from src.policy.models import Policy
from src.run_report.models import GateResult

KIND_LABEL = {"postgres": "bảng Postgres", "rest_api": "endpoint REST", "filesystem": "thư mục file"}


def _mentions(request: str, name: str, aliases: list[str]) -> bool:
    words = [name.replace("_", " "), name, *aliases]
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", request, re.IGNORECASE) for word in words)


def _describe(policy: Policy, domain: str, entity: str) -> str:
    decl = policy.domains.domains[domain]
    kinds: dict[str, int] = {}
    for ref in decl.entities[entity].tables:
        kind = decl.sources[ref.partition(".")[0]].kind
        kinds[kind] = kinds.get(kind, 0) + 1
    where = ", ".join(f"{count} {KIND_LABEL.get(kind, kind)}" for kind, count in kinds.items())
    return f"{domain.capitalize()} định nghĩa là {decl.entities[entity].definition} ({where})"


def entity_scan(request: str, scope: list[dict] | None, policy: Policy) -> tuple[GateResult, str | None]:
    """Trả (cổng semantic, thông điệp chặn hoặc None)."""
    registry = policy.domains
    scoped = {registry.domain_of_source(choice["source_ref"]) for choice in scope or []} - {None}
    conflicts, resolved = [], {}
    by_entity: dict[str, list[str]] = {}
    for domain_name, domain in registry.domains.items():
        for entity in domain.entities:
            by_entity.setdefault(entity, []).append(domain_name)
    for entity, domains in sorted(by_entity.items()):
        if len(domains) < 2 or entity in registry.conformed_dimensions:
            continue
        aliases = sorted({a for d in domains for a in registry.domains[d].entities[entity].aliases})
        if not _mentions(request, entity, aliases):
            continue
        if len(scoped) == 1 and next(iter(scoped)) in domains:
            resolved[entity] = next(iter(scoped))
            continue
        conflicts.append((entity, domains))

    observed: dict = {"resolved": resolved}
    if not conflicts:
        detail = "; ".join(
            f"'{entity}' dùng định nghĩa của domain {domain} theo nguồn đã chọn: "
            f"{registry.domains[domain].entities[entity].definition}"
            for entity, domain in resolved.items()
        )
        return GateResult(
            name="semantic", result="pass", detail=detail or "Không thực thể nào mơ hồ", observed=observed
        ), None

    messages = []
    for entity, domains in conflicts:
        definitions = ". ".join(_describe(policy, d, entity) for d in domains)
        stewards = ", ".join(f"{d.capitalize()}: {registry.domains[d].steward}" for d in domains)
        messages.append(
            f"Phát hiện thực thể `{entity}` ở {len(domains)} domain. {definitions}. Chưa có định nghĩa chuẩn "
            f"(conformed_dimensions). Chuyển tới steward {stewards}; hoặc chọn nguồn của đúng một domain rồi tạo lại."
        )
        observed[entity] = {
            "domains": {
                d: {
                    "definition": registry.domains[d].entities[entity].definition,
                    "tables": list(registry.domains[d].entities[entity].tables),
                    "steward": registry.domains[d].steward,
                }
                for d in domains
            }
        }
    message = " ".join(messages)
    return GateResult(name="semantic", result="fail", detail=message, observed=observed), message
