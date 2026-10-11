"""Dagster GraphQL giả: chạy offline, thời gian chờ được điều khiển để test không ngủ thật."""

import httpx
import pytest

from src.deployer import verify


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Dagster:
    def __init__(self, *statuses):
        self.statuses = list(statuses or ["SUCCESS"])
        self.calls = []
        self.error_query = None
        self.error = None
        self.on_status = lambda: None

    @property
    def launches(self):
        return [body["variables"]["executionParams"] for _, body, _ in self.calls if body["query"] == verify.LAUNCH]

    def __call__(self, url, *, json, timeout):
        self.calls.append((url, json, timeout))
        query = json["query"]
        if query == self.error_query:
            if isinstance(self.error, Exception):
                raise self.error
            return httpx.Response(200, json=self.error, request=httpx.Request("POST", url))
        if query == verify.RELOAD:
            body = {
                "reloadWorkspace": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": model,
                            "locationOrLoadError": {
                                "__typename": "RepositoryLocation",
                                "repositories": [{"name": "__repository__", "pipelines": [{"name": f"{model}_job"}]}],
                            },
                        }
                        for model in ("fct_x", "fct_y")
                    ],
                }
            }
        elif query == verify.LAUNCH:
            body = {"launchPipelineExecution": {"__typename": "LaunchRunSuccess", "run": {"runId": "dagster-run-1"}}}
        elif query == verify.STATUS:
            self.on_status()
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            body = {"runOrError": {"__typename": "Run", "runId": "dagster-run-1", "status": status}}
        elif query == verify.TERMINATE:
            body = {"terminateRun": {"__typename": "TerminateRunSuccess"}}
        else:
            pytest.fail("Query ngoài luồng chạy thử")
        return httpx.Response(200, json={"data": body}, request=httpx.Request("POST", url))


def run(monkeypatch, dagster, timeout=120):
    clock = Clock()
    monkeypatch.setattr(verify.httpx, "post", dagster)
    return verify.verify_pipeline("http://dagster:3070/", "fct_x", timeout, sleep=clock.sleep, clock=clock)


def test_success_launches_only_the_installed_job_once_and_waits(monkeypatch):
    dagster = Dagster("QUEUED", "STARTED", "SUCCESS")

    assert run(monkeypatch, dagster) == "dagster-run-1"

    assert dagster.launches == [
        {
            "selector": {
                "repositoryLocationName": "fct_x",
                "repositoryName": "__repository__",
                "pipelineName": "fct_x_job",
            },
            "runConfigData": {},
            "mode": "default",
        }
    ]
    assert [body["query"] for _, body, _ in dagster.calls] == [verify.RELOAD, verify.LAUNCH, *[verify.STATUS] * 3]
    assert all(url == "http://dagster:3070/graphql" and 0 < timeout <= 10 for url, _, timeout in dagster.calls)
    assert all(body["variables"] == {"runId": "dagster-run-1"} for _, body, _ in dagster.calls[2:])


@pytest.mark.parametrize("status", ["FAILURE", "CANCELED"])
def test_terminal_failure_never_launches_again(monkeypatch, status):
    dagster = Dagster(status)

    with pytest.raises(verify.VerificationError, match=status):
        run(monkeypatch, dagster)

    assert len(dagster.launches) == 1


def test_timeout_uses_total_deadline_and_requests_cancellation(monkeypatch):
    dagster, clock = Dagster("STARTED"), Clock()
    monkeypatch.setattr(verify.httpx, "post", dagster)

    with pytest.raises(verify.VerificationError, match="quá 9 giây"):
        verify.verify_pipeline("http://dagster:3070", "fct_x", 9, sleep=clock.sleep, clock=clock)

    assert clock.now == 9 and len(dagster.launches) == 1
    assert dagster.calls[-1][1] == {"query": verify.TERMINATE, "variables": {"runId": "dagster-run-1"}}
    assert [timeout for _, body, timeout in dagster.calls if body["query"] == verify.STATUS] == [5, 3, 1]


def test_preparation_waits_for_workspace_restart_without_repeating_launch(monkeypatch):
    dagster, clock = Dagster(), Clock()
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"]["query"])
        assert clock.now >= verify.WORKSPACE_SETTLE_SEC
        if len(calls) == 1:
            raise httpx.ConnectError("Dagster đang restart")
        return dagster(url, **kwargs)

    monkeypatch.setattr(verify.httpx, "post", post)

    assert (
        verify.verify_pipeline("http://dagster:3070", "fct_x", 120, sleep=clock.sleep, clock=clock) == "dagster-run-1"
    )
    assert calls == [verify.RELOAD, verify.RELOAD, verify.LAUNCH, verify.STATUS] and len(dagster.launches) == 1


@pytest.mark.parametrize("query", [verify.RELOAD, verify.LAUNCH, verify.STATUS])
@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("secret-url"),
        httpx.ReadTimeout("secret-url"),
        {"errors": [{"message": "secret-url"}]},
        {"data": None},
        [],
    ],
)
def test_connection_or_graphql_errors_are_bounded_and_sanitized(monkeypatch, query, error):
    dagster = Dagster()
    dagster.error_query, dagster.error = query, error

    with pytest.raises(verify.VerificationError, match="Dagster") as exc:
        run(monkeypatch, dagster)

    assert "secret-url" not in str(exc.value)
    assert len(dagster.launches) <= 1


@pytest.mark.parametrize(
    ("query", "data"),
    [
        (verify.RELOAD, {"reloadWorkspace": {"__typename": "PythonError", "message": "secret-url"}}),
        (verify.RELOAD, {"reloadWorkspace": {"__typename": "Workspace", "locationEntries": []}}),
        (verify.LAUNCH, {"launchPipelineExecution": {"__typename": "RunConfigValidationInvalid"}}),
        (verify.LAUNCH, {"launchPipelineExecution": {"__typename": "LaunchRunSuccess", "run": {}}}),
        (verify.STATUS, {"runOrError": {"__typename": "RunNotFoundError"}}),
        (verify.STATUS, {"runOrError": {"__typename": "Run", "runId": "other", "status": "SUCCESS"}}),
        (verify.STATUS, {"runOrError": {"__typename": "Run", "runId": "dagster-run-1", "status": {}}}),
    ],
)
def test_invalid_results_fail_without_raw_dagster_messages(monkeypatch, query, data):
    dagster = Dagster()
    dagster.error_query, dagster.error = query, {"data": data}

    with pytest.raises(verify.VerificationError) as exc:
        run(monkeypatch, dagster)

    assert "secret-url" not in str(exc.value)


def test_http_and_invalid_json_fail_clearly(monkeypatch):
    for response, reason in [
        (httpx.Response(503, text="secret-url"), "HTTP"),
        (httpx.Response(200, text="secret-url"), "JSON"),
    ]:
        response.request = httpx.Request("POST", "http://dagster:3070/graphql")
        monkeypatch.setattr(verify.httpx, "post", lambda *a, **k: response)
        with pytest.raises(verify.VerificationError, match=reason) as exc:
            verify.graphql("http://dagster:3070", verify.RELOAD, {}, 3)
        assert "secret-url" not in str(exc.value)


def test_queries_validate_against_installed_dagster_schema():
    from dagster_graphql.schema import create_schema
    from graphql import parse, validate

    schema = create_schema().graphql_schema
    for query in (verify.RELOAD, verify.LAUNCH, verify.STATUS, verify.TERMINATE):
        assert validate(schema, parse(query)) == []
