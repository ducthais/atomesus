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
        self.executions = []
        self.error_query = None
        self.error = None
        self.on_status = lambda: None

    @property
    def launches(self):
        return [
            body["variables"]["executionParams"]
            for _, body, _ in self.calls
            if body["query"] == verify.LAUNCH
            and any(body["variables"]["executionParams"] is execution for execution in self.executions)
        ]

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
            self.executions.append(json["variables"]["executionParams"])
            body = {"launchPipelineExecution": {"__typename": "LaunchRunSuccess", "run": {"runId": "dagster-run-1"}}}
        elif query == verify.FIND_RUN:
            tags = json["variables"]["filter"]["tags"]
            found = any(execution["executionMetadata"]["tags"] == tags for execution in self.executions)
            body = {
                "runsOrError": {
                    "__typename": "Runs",
                    "results": [{"runId": "dagster-run-1"}] if found else [],
                }
            }
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
            "executionMetadata": dagster.launches[0]["executionMetadata"],
        }
    ]
    [tag] = dagster.launches[0]["executionMetadata"]["tags"]
    assert tag["key"] == "atomesus/verify" and len(tag["value"]) == 32
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
    assert [timeout for _, body, timeout in dagster.calls if body["query"] == verify.STATUS] == [9, 7, 5, 3, 1]


def test_preparation_waits_for_workspace_restart_without_repeating_launch(monkeypatch):
    dagster, clock = Dagster(), Clock()
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["json"]["query"])
        if len(calls) <= 4:
            raise httpx.ConnectError("Dagster đang restart")
        return dagster(url, **kwargs)

    monkeypatch.setattr(verify.httpx, "post", post)

    assert (
        verify.verify_pipeline("http://dagster:3070", "fct_x", 120, sleep=clock.sleep, clock=clock) == "dagster-run-1"
    )
    assert calls == [*[verify.RELOAD] * 5, verify.LAUNCH, verify.STATUS] and len(dagster.launches) == 1
    assert clock.now == 8


@pytest.mark.parametrize("query", [verify.RELOAD, verify.LAUNCH, verify.STATUS])
def test_transient_connection_errors_retry_within_the_same_deadline(monkeypatch, query):
    dagster, clock = Dagster("STARTED", "SUCCESS"), Clock()
    failures = 0

    def post(url, **kwargs):
        nonlocal failures
        if kwargs["json"]["query"] == query and failures < 3:
            failures += 1
            raise httpx.ConnectError("secret-url")
        return dagster(url, **kwargs)

    monkeypatch.setattr(verify.httpx, "post", post)

    assert verify.verify_pipeline("http://dagster:3070", "fct_x", 12, sleep=clock.sleep, clock=clock) == "dagster-run-1"
    assert clock.now == 8 and failures == 3 and len(dagster.launches) == 1
    assert not any(body["query"] == verify.TERMINATE for _, body, _ in dagster.calls)


def test_workspace_location_must_be_loaded_before_launch(monkeypatch):
    dagster, clock = Dagster(), Clock()
    reloads = 0

    def post(url, **kwargs):
        nonlocal reloads
        response = dagster(url, **kwargs)
        if kwargs["json"]["query"] == verify.RELOAD:
            reloads += 1
            body = response.json()
            if reloads == 1:
                body["data"]["reloadWorkspace"]["locationEntries"] = []
            elif reloads == 2:
                body["data"]["reloadWorkspace"]["locationEntries"][0]["locationOrLoadError"] = {
                    "__typename": "PythonError",
                    "message": "secret-url",
                }
            response = httpx.Response(200, json=body, request=response.request)
        if kwargs["json"]["query"] == verify.LAUNCH:
            assert reloads == 3
        return response

    monkeypatch.setattr(verify.httpx, "post", post)

    assert verify.verify_pipeline("http://dagster:3070", "fct_x", 12, sleep=clock.sleep, clock=clock) == "dagster-run-1"
    assert clock.now == 4 and len(dagster.launches) == 1


def test_lost_launch_response_recovers_run_by_tag_without_relaunch(monkeypatch):
    dagster, clock = Dagster(), Clock()
    searches = 0

    def post(url, **kwargs):
        nonlocal searches
        response = dagster(url, **kwargs)
        if kwargs["json"]["query"] == verify.LAUNCH:
            raise httpx.ReadTimeout("secret-url")
        if kwargs["json"]["query"] == verify.FIND_RUN:
            searches += 1
            assert kwargs["json"]["variables"]["filter"]["tags"] == dagster.launches[0]["executionMetadata"]["tags"]
            if searches < 3:
                raise httpx.ConnectError("restart")
        return response

    monkeypatch.setattr(verify.httpx, "post", post)

    assert verify.verify_pipeline("http://dagster:3070", "fct_x", 12, sleep=clock.sleep, clock=clock) == "dagster-run-1"
    assert len(dagster.launches) == 1 and searches == 3 and clock.now == 4


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
    dagster, clock = Dagster(), Clock()
    dagster.error_query, dagster.error = query, error
    monkeypatch.setattr(verify.httpx, "post", dagster)

    with pytest.raises(verify.VerificationError, match="Dagster") as exc:
        verify.verify_pipeline("http://dagster:3070", "fct_x", 9, sleep=clock.sleep, clock=clock)

    assert "secret-url" not in str(exc.value)
    assert clock.now == 9 and "quá 9 giây" in str(exc.value)
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
    for query in (verify.RELOAD, verify.LAUNCH, verify.STATUS, verify.FIND_RUN, verify.TERMINATE):
        assert validate(schema, parse(query)) == []
