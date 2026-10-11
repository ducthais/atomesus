"""Chạy thử đúng job của pipeline vừa cài trên Dagster production (SPEC §9.5 mức D2)."""

import time
from contextvars import ContextVar
from math import isfinite
from uuid import uuid4

import httpx

WORKSPACE = """
query VerifyWorkspaceReady {
  workspaceOrError {
    __typename
    ... on Workspace {
      locationEntries {
        name
        loadStatus
        updatedTimestamp
        locationOrLoadError {
          __typename
          ... on RepositoryLocation { repositories { name pipelines { name } } }
        }
      }
    }
  }
}
"""
RELOAD = """
mutation VerifyWorkspace {
  reloadWorkspace {
    __typename
    ... on Workspace {
      locationEntries {
        name
        locationOrLoadError {
          __typename
          ... on RepositoryLocation { repositories { name pipelines { name } } }
        }
      }
    }
  }
}
"""
LAUNCH = """
mutation VerifyLaunch($executionParams: ExecutionParams!) {
  launchPipelineExecution(executionParams: $executionParams) {
    __typename
    ... on LaunchRunSuccess { run { runId } }
  }
}
"""
STATUS = """
query VerifyStatus($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run { runId status }
  }
}
"""
FIND_RUN = """
query VerifyFindRun($filter: RunsFilter!) {
  runsOrError(filter: $filter, limit: 2) {
    __typename
    ... on Runs { results { runId } }
  }
}
"""
TERMINATE = """
mutation VerifyTerminate($runId: String!) {
  terminateRun(runId: $runId) { __typename }
}
"""
REQUEST_TIMEOUT_SEC = 10
POLL_SEC = 2
# Truyền mốc cài qua callback hiện có, riêng từng ngữ cảnh; không đổi API install/verify.
WORKSPACE_UPDATED_AFTER: ContextVar[float | None] = ContextVar("workspace_updated_after", default=None)
LAUNCH_ERRORS = {
    "RunConfigValidationInvalid",
    "PipelineNotFoundError",
    "InvalidStepError",
    "InvalidOutputError",
    "ConflictingExecutionParamsError",
    "PresetNotFoundError",
    "PythonError",
    "UnauthorizedError",
    "RunConflict",
    "NoModeProvidedError",
    "InvalidSubsetError",
}


class VerificationError(RuntimeError):
    """Chạy thử không xanh; thông báo ngắn, không chứa nội dung tùy ý từ Dagster."""


class DagsterRejectedError(VerificationError):
    """Lỗi nạp location hoặc từ chối launch dứt khoát; chờ thêm không giải quyết được."""


class DagsterUnavailableError(VerificationError):
    """Dagster có thể đang restart; chỉ gửi lại launch nếu chưa kết nối được."""

    def __init__(self, message: str, *, retry_launch: bool = False):
        super().__init__(message)
        self.retry_launch = retry_launch


def graphql(url: str, query: str, variables: dict, timeout: float) -> dict:
    """Gọi nội bộ, có timeout; không đưa URL hoặc lỗi thô (có thể chứa secret) vào callback/log."""
    try:
        response = httpx.post(
            url.rstrip("/") + "/graphql", json={"query": query, "variables": variables}, timeout=timeout
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise DagsterUnavailableError(
            "Dagster không trả lời hoặc trả lỗi HTTP khi chạy thử.",
            retry_launch=isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)),
        ) from None
    except ValueError:
        raise VerificationError("Dagster trả phản hồi không phải JSON khi chạy thử.") from None
    if not isinstance(body, dict) or body.get("errors") or not isinstance(body.get("data"), dict):
        raise VerificationError("Dagster trả lỗi GraphQL hoặc thiếu dữ liệu khi chạy thử.")
    return body["data"]


def _section(data: dict, field: str, kind: str) -> dict:
    section = data.get(field)
    if not isinstance(section, dict) or section.get("__typename") != kind:
        raise VerificationError(f"Dagster trả lỗi GraphQL ở {field} khi chạy thử.")
    return section


def verify_pipeline(url: str, model: str, timeout: int, *, sleep=time.sleep, clock=time.monotonic) -> str:
    """Nạp code mới, launch một lần, chỉ trả run id khi SUCCESS. Mọi bước dùng chung hạn chờ."""
    deadline = clock() + timeout
    updated_after = WORKSPACE_UPDATED_AFTER.get()
    run_id = None
    finished = False
    tag = {"key": "atomesus/verify", "value": uuid4().hex}

    def pause(exc: VerificationError) -> None:
        if clock() >= deadline:
            raise VerificationError(f"Chạy thử Dagster quá {timeout} giây. {exc}") from None
        sleep(min(POLL_SEC, max(0, deadline - clock())))

    def request(query: str, variables: dict) -> dict:
        unavailable = None
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                reason = f" {unavailable}" if unavailable else ""
                raise VerificationError(f"Chạy thử Dagster quá {timeout} giây.{reason}")
            try:
                data = graphql(url, query, variables, min(REQUEST_TIMEOUT_SEC, remaining))
            except VerificationError as exc:
                if query == LAUNCH and not (isinstance(exc, DagsterUnavailableError) and exc.retry_launch):
                    raise
                unavailable = str(exc)
                pause(exc)
                continue
            if clock() >= deadline:
                raise VerificationError(f"Chạy thử Dagster quá {timeout} giây.")
            return data

    try:
        job = f"{model}_job"
        while True:
            try:
                query, field = (
                    (WORKSPACE, "workspaceOrError") if updated_after is not None else (RELOAD, "reloadWorkspace")
                )
                workspace = _section(request(query, {}), field, "Workspace")
                entries = workspace.get("locationEntries")
                if not isinstance(entries, list):
                    raise VerificationError("Dagster thiếu danh sách code location khi chạy thử.")
                location = next(
                    (entry for entry in entries if isinstance(entry, dict) and entry.get("name") == model), {}
                )
                if updated_after is not None:
                    stamp = location.get("updatedTimestamp")
                    if type(stamp) not in (int, float) or not isfinite(stamp) or stamp < updated_after:
                        raise VerificationError(f"Chờ Dagster nạp code location {model} sau khi workspace.yaml đổi.")
                loaded = location.get("locationOrLoadError")
                if isinstance(loaded, dict) and loaded.get("__typename") == "PythonError":
                    raise DagsterRejectedError(f"Dagster nạp code location {model} lỗi (PythonError).")
                if updated_after is not None and location.get("loadStatus") != "LOADED":
                    raise VerificationError(f"Chờ Dagster nạp code location {model} (LOADED).")
                loaded = _section(location, "locationOrLoadError", "RepositoryLocation")
                repositories = loaded.get("repositories")
                if not isinstance(repositories, list):
                    raise VerificationError("Dagster thiếu danh sách repository khi chạy thử.")
                matches = [
                    repo["name"]
                    for repo in repositories
                    if isinstance(repo, dict)
                    and isinstance(repo.get("name"), str)
                    and isinstance(repo.get("pipelines"), list)
                    and any(isinstance(p, dict) and p.get("name") == job for p in repo["pipelines"])
                ]
                if len(matches) != 1:
                    raise VerificationError(f"Dagster không tìm thấy duy nhất job {job} trong code location {model}.")
                break
            except DagsterRejectedError:
                raise
            except VerificationError as exc:
                pause(exc)
        try:
            data = request(
                LAUNCH,
                {
                    "executionParams": {
                        "selector": {
                            "repositoryLocationName": model,
                            "repositoryName": matches[0],
                            "pipelineName": job,
                        },
                        "runConfigData": {},
                        "mode": "default",
                        "executionMetadata": {"tags": [tag]},
                    }
                },
            )
            launched = data.get("launchPipelineExecution")
            if isinstance(launched, dict) and launched.get("__typename") != "LaunchRunSuccess":
                kind = launched.get("__typename")
                reason = kind if isinstance(kind, str) and kind in LAUNCH_ERRORS else "phản hồi không hợp lệ"
                raise DagsterRejectedError(f"Dagster từ chối kích hoạt chạy thử ({reason}).")
            launched = _section(data, "launchPipelineExecution", "LaunchRunSuccess")
            run = launched.get("run")
            if not isinstance(run, dict) or not isinstance(run.get("runId"), str) or not run["runId"]:
                raise VerificationError("Dagster không trả run id sau khi kích hoạt chạy thử.")
            run_id = run["runId"]
        except DagsterRejectedError:
            raise
        except VerificationError as launch_error:
            # Mất phản hồi có thể đã tạo run: tìm theo tag, không gửi lại mutation gây chạy trùng.
            while run_id is None:
                try:
                    found = _section(request(FIND_RUN, {"filter": {"tags": [tag]}}), "runsOrError", "Runs")
                    results = found.get("results")
                    if not isinstance(results, list) or len(results) != 1:
                        raise launch_error
                    candidate = results[0].get("runId") if isinstance(results[0], dict) else None
                    if not isinstance(candidate, str) or not candidate:
                        raise launch_error
                    run_id = candidate
                except VerificationError as exc:
                    pause(exc)
        while True:
            try:
                run = _section(request(STATUS, {"runId": run_id}), "runOrError", "Run")
                if run.get("runId") != run_id:
                    raise VerificationError("Dagster trả sai run id khi chờ chạy thử.")
                status = run.get("status")
                if not isinstance(status, str) or status not in {
                    "SUCCESS",
                    "FAILURE",
                    "CANCELED",
                    "QUEUED",
                    "NOT_STARTED",
                    "STARTING",
                    "STARTED",
                    "CANCELING",
                    "MANAGED",
                }:
                    raise VerificationError("Dagster trả trạng thái chạy thử không hợp lệ.")
            except VerificationError as exc:
                pause(exc)
                continue
            if status == "SUCCESS":
                return run_id
            if status in {"FAILURE", "CANCELED"}:
                finished = True
                raise VerificationError(f"Chạy thử Dagster kết thúc {status} (run {run_id}).")
            sleep(min(POLL_SEC, max(0, deadline - clock())))
    except VerificationError as exc:
        if run_id is not None and not finished:
            # Dừng run còn chạy trước khi quay về code cũ; không launch lại khi mất phản hồi.
            try:
                graphql(url, TERMINATE, {"runId": run_id}, 3)
            except VerificationError:
                pass
        detail = str(exc)
        if run_id is not None and run_id not in detail:
            detail += f" (run {run_id})"
        raise VerificationError(detail) from None
