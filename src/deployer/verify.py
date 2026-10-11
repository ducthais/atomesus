"""Chạy thử đúng job của pipeline vừa cài trên Dagster production (SPEC §9.5 mức D2)."""

import time

import httpx

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
TERMINATE = """
mutation VerifyTerminate($runId: String!) {
  terminateRun(runId: $runId) { __typename }
}
"""
REQUEST_TIMEOUT_SEC = 10
POLL_SEC = 2
# scripts/dagster_local.py kiểm tra workspace mỗi 3 giây; chờ nhịp đó trước khi launch bản vừa cài.
WORKSPACE_SETTLE_SEC = 4


class VerificationError(RuntimeError):
    """Chạy thử không xanh; thông báo ngắn, không chứa nội dung tùy ý từ Dagster."""


class DagsterUnavailableError(VerificationError):
    """Có thể đang restart sau khi workspace đổi; chỉ bước chuẩn bị được thử lại."""


def graphql(url: str, query: str, variables: dict, timeout: float) -> dict:
    """Gọi nội bộ, có timeout; không đưa URL hoặc lỗi thô (có thể chứa secret) vào callback/log."""
    try:
        response = httpx.post(
            url.rstrip("/") + "/graphql", json={"query": query, "variables": variables}, timeout=timeout
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError:
        raise DagsterUnavailableError("Dagster không trả lời hoặc trả lỗi HTTP khi chạy thử.") from None
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
    run_id = None
    finished = False

    def request(query: str, variables: dict) -> dict:
        unavailable = None
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                reason = f" {unavailable}" if unavailable else ""
                raise VerificationError(f"Chạy thử Dagster quá {timeout} giây.{reason}")
            try:
                data = graphql(url, query, variables, min(REQUEST_TIMEOUT_SEC, remaining))
            except DagsterUnavailableError as exc:
                if query != RELOAD:
                    raise
                unavailable = str(exc)
                sleep(min(POLL_SEC, max(0, deadline - clock())))
                continue
            if clock() >= deadline:
                raise VerificationError(f"Chạy thử Dagster quá {timeout} giây.")
            return data

    try:
        sleep(min(WORKSPACE_SETTLE_SEC, timeout))
        workspace = _section(request(RELOAD, {}), "reloadWorkspace", "Workspace")
        entries = workspace.get("locationEntries")
        if not isinstance(entries, list):
            raise VerificationError("Dagster thiếu danh sách code location khi chạy thử.")
        location = next((entry for entry in entries if isinstance(entry, dict) and entry.get("name") == model), {})
        loaded = _section(location, "locationOrLoadError", "RepositoryLocation")
        job = f"{model}_job"
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
        launched = _section(
            request(
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
                    }
                },
            ),
            "launchPipelineExecution",
            "LaunchRunSuccess",
        )
        run = launched.get("run")
        if not isinstance(run, dict) or not isinstance(run.get("runId"), str) or not run["runId"]:
            raise VerificationError("Dagster không trả run id sau khi kích hoạt chạy thử.")
        run_id = run["runId"]
        while True:
            run = _section(request(STATUS, {"runId": run_id}), "runOrError", "Run")
            if run.get("runId") != run_id:
                raise VerificationError("Dagster trả sai run id khi chờ chạy thử.")
            status = run.get("status")
            if not isinstance(status, str):
                raise VerificationError("Dagster trả trạng thái chạy thử không hợp lệ.")
            if status == "SUCCESS":
                return run_id
            if status in {"FAILURE", "CANCELED"}:
                finished = True
                raise VerificationError(f"Chạy thử Dagster kết thúc {status} (run {run_id}).")
            if status not in {"QUEUED", "NOT_STARTED", "STARTING", "STARTED", "CANCELING", "MANAGED"}:
                raise VerificationError("Dagster trả trạng thái chạy thử không hợp lệ.")
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
