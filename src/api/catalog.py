from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from src.codegen import reference
from src.pipeline import connectors, postgres
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import PolicyError, effective_policy
from src.policy.models import Policy
from src.run_report.access import model_visible, run_visible, visible_run_ids
from src.run_report.compare import model_of
from src.run_report.drift import detect, live_pipelines
from src.run_report.store import list_run_reports
from src.spec.models import Spec

router = APIRouter(prefix="/catalog", tags=["catalog"], dependencies=[Depends(current_user)])


def _policy(config: PipelineSettings, user: dict | None = None) -> Policy:
    try:
        return effective_policy(config, owner_id=user["id"] if user and user["role"] == "Engineer" else None)
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc


def _location(decl) -> str | None:
    return decl.schema_ or decl.openapi or decl.root


def _selected_objects(decl, found: dict) -> dict:
    """Không trả metadata của bảng ngoài lựa chọn, kể cả bảng được khóa ngoại nhắc tới."""
    if decl.tables is None:
        return found
    allowed = set(decl.tables)
    return {
        **found,
        "objects": [obj for obj in found["objects"] if obj["name"] in allowed],
        "foreign_keys": [
            key
            for key in found["foreign_keys"]
            if key["table"] in allowed and key["ref_schema"] == decl.schema_ and key["ref_table"] in allowed
        ],
    }


@router.get("")
def read_catalog(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Domain → nguồn → bảng/cột thật, kèm steward và thực thể từ domains.yml. Chỉ đọc, không gọi LLM.

    Nguồn có connector được kết nối để đọc schema; nguồn chưa hỗ trợ hoặc không kết nối được
    vẫn có mặt với `available: false` và lý do, để giao diện catalog hiện đủ những gì đã khai báo.
    """
    policy = _policy(config, user)
    domains = {}
    for name, domain in policy.domains.domains.items():
        sources = {}
        for ref, decl in domain.sources.items():
            entry: dict = {
                "kind": decl.kind,
                "connection_ref": decl.connection_ref,
                "location": _location(decl),
                "available": False,
                "tables": {},
                "primary_keys": {},
                "foreign_keys": [],
            }
            try:
                found = _selected_objects(decl, connectors.discover(decl, config))
                entry["tables"] = {obj["name"]: obj["columns"] for obj in found["objects"]}
                entry["primary_keys"] = {
                    obj["name"]: obj["primary_key"] for obj in found["objects"] if obj["primary_key"]
                }
                entry["foreign_keys"] = found["foreign_keys"]
                entry["available"] = True
            except connectors.UnsupportedSourceError:
                entry["reason"] = f"Chưa có connector cho nguồn {decl.kind}"
            except postgres.SourceUnavailableError as exc:
                entry["reason"] = str(exc)
            sources[ref] = entry
        domains[name] = {
            "steward": domain.steward,
            "currency": domain.currency.base,
            "sources": sources,
            "entities": {key: entity.model_dump() for key, entity in domain.entities.items()},
        }
        if domain.label is not None:
            domains[name]["label"] = domain.label
    return {"domains": domains, "conformed_dimensions": policy.domains.conformed_dimensions}


@router.get("/sources")
def list_sources(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> list[dict]:
    """Các kết nối khai báo trong domains.yml. Không kết nối tới nguồn; dùng để chọn nguồn trước khi khám phá."""
    return [
        {
            "ref": ref,
            "domain": name,
            "kind": decl.kind,
            "connection_ref": decl.connection_ref,
            "location": _location(decl),
            "supported": connectors.supported(decl.kind),
        }
        for name, domain in _policy(config, user).domains.domains.items()
        for ref, decl in domain.sources.items()
    ]


@router.get("/sources/{ref}/objects")
def source_objects(ref: str, user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Kết nối trực tiếp tới nguồn lúc được gọi và trả các bảng (hoặc collection) đang có, kèm cột và khóa."""
    declared = {
        r: (name, decl)
        for name, domain in _policy(config, user).domains.domains.items()
        for r, decl in domain.sources.items()
    }
    if ref not in declared:
        raise HTTPException(404, f"Nguồn '{ref}' không có trong domains.yml.")
    domain, decl = declared[ref]
    try:
        found = _selected_objects(decl, connectors.discover(decl, config))
    except connectors.UnsupportedSourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    except postgres.SourceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "ref": ref,
        "domain": domain,
        "kind": decl.kind,
        "location": _location(decl),
        "discovered_at": datetime.now(UTC).isoformat(),
        **found,
    }


@router.get("/drift")
def schema_drift(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)) -> dict:
    """Pipeline đang chạy nào bị ảnh hưởng vì schema nguồn đổi (bảng / cột bị xóa, đổi tên, đổi kiểu).

    Kết nối từng nguồn một lần lúc gọi; nguồn không đọc được thì pipeline dùng nó có trạng thái `unknown`.
    """
    declared = {
        r: decl for domain in _policy(config, user).domains.domains.values() for r, decl in domain.sources.items()
    }
    cache: dict[str, dict | Exception] = {}

    def tables_of(ref: str) -> dict[str, dict[str, str]]:
        if ref not in cache:
            try:
                if ref not in declared:
                    raise connectors.UnsupportedSourceError(f"Nguồn '{ref}' không còn trong domains.yml.")
                found = _selected_objects(declared[ref], connectors.discover(declared[ref], config))
                cache[ref] = {o["name"]: {c["name"]: c["type"] for c in o["columns"]} for o in found["objects"]}
            except (connectors.UnsupportedSourceError, postgres.SourceUnavailableError) as exc:
                cache[ref] = exc
        if isinstance(cache[ref], Exception):
            raise cache[ref]
        return cache[ref]

    # Xét bản đang chạy trên mọi run rồi mới lọc quyền: bản cũ của mình không được coi là đang chạy khi đã bị thay.
    visible = visible_run_ids(user, config.store_path)
    live = [
        report
        for report in live_pipelines(list_run_reports(config.store_path))
        if (visible is None or report.run_id in visible) and model_visible(user, config.store_path, model_of(report))
    ]
    pipelines = [detect(report, tables_of) for report in live]
    return {"checked_at": datetime.now(UTC).isoformat(), "pipelines": pipelines}


@router.post("/datasets/{domain}/{model}/reconcile")
def reconcile_deployed(
    domain: str,
    model: str,
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> dict:
    """Đối chiếu lại bảng gold Dagster đang giữ với kết quả tính lại thẳng trên nguồn Postgres, ngay lúc gọi.

    Dùng Spec của bản đang chạy (bản duyệt mới nhất của model). Không ghi gì: chỉ đọc warehouse và nguồn.
    """
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được đối soát pipeline đã deploy.")
    import duckdb

    # gold.<model> là một bảng chung cho mọi domain: bản đang ghi bảng là bản deploy duyệt sau cùng của tên model. Chỉ
    # đọc bảng khi người gọi được thấy đúng bản đó và tên model (xem src.run_report.access.model_owner).
    live = [r for r in live_pipelines(list_run_reports(config.store_path, limit=1000)) if model_of(r) == model]
    report = max(live, key=lambda r: r.approval.at, default=None)
    if (
        report is None
        or report.domain != domain
        or not run_visible(user, config.store_path, report.run_id)
        or not model_visible(user, config.store_path, model)
    ):
        raise HTTPException(404, f"Không có pipeline đã duyệt nào tạo gold.{model} trong domain {domain}.")
    base = {"run_id": report.run_id, "model": model, "checked_at": datetime.now(UTC).isoformat()}
    try:
        with duckdb.connect(config.warehouse_path, read_only=True) as db:
            gold = reference.read_gold(db, model)
    except duckdb.CatalogException:
        return {
            **base,
            "available": False,
            "reason": f"Dagster chưa materialize gold.{model}: chạy job trong Dagster trước.",
        }
    except duckdb.Error as exc:
        reason = "Dagster đang ghi warehouse, thử lại sau ít giây." if "lock" in str(exc).lower() else str(exc)
        return {**base, "available": False, "reason": reason}
    try:
        matched, observed = reference.reconcile(Spec.model_validate(report.spec), gold, _policy(config, user), config)
    except reference.UnsupportedError as exc:
        return {**base, "available": False, "reason": f"Chưa đối chiếu được: {exc}."}
    except postgres.SourceUnavailableError as exc:
        return {**base, "available": False, "reason": str(exc)}
    return {**base, "available": True, "matched": matched, "observed": observed}
