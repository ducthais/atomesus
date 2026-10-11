"""Dagster GraphQL giả: chạy offline, thời gian chờ được điều khiển để test không ngủ thật."""

import time

import httpx
import pytest

from src.deployer import install, verify


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
        if query in (verify.RELOAD, verify.WORKSPACE):
            body = {
                "reloadWorkspace" if query == verify.RELOAD else "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": model,
                            "loadStatus": "LOADED",
                            "updatedTimestamp": time.time(),
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


@pytest.mark.parametrize("query", [verify.WORKSPACE, verify.RELOAD, verify.LAUNCH, verify.STATUS])
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

    context = verify.WORKSPACE_UPDATED_AFTER.set(0) if query == verify.WORKSPACE else None
    try:
        assert (
            verify.verify_pipeline("http://dagster:3070", "fct_x", 12, sleep=clock.sleep, clock=clock)
            == "dagster-run-1"
        )
    finally:
        if context is not None:
            verify.WORKSPACE_UPDATED_AFTER.reset(context)
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
                    "__typename": "RepositoryLocation",
                    "repositories": [],
                }
            response = httpx.Response(200, json=body, request=response.request)
        if kwargs["json"]["query"] == verify.LAUNCH:
            assert reloads == 3
        return response

    monkeypatch.setattr(verify.httpx, "post", post)

    assert verify.verify_pipeline("http://dagster:3070", "fct_x", 12, sleep=clock.sleep, clock=clock) == "dagster-run-1"
    assert clock.now == 4 and len(dagster.launches) == 1


@pytest.mark.parametrize("first_install", [False, True])
def test_install_waits_for_new_process_before_any_mutation(tmp_path, monkeypatch, first_install):
    dagster, clock = Dagster(), Clock()
    asset = "infra/dagster/generated/fct_x_asset.py"

    def prepare(root, files):
        target = root / asset
        target.parent.mkdir(parents=True)
        target.write_text(files[asset], encoding="utf-8")

    monkeypatch.setattr(install, "prepare_project", prepare)
    monkeypatch.setattr(install, "_compile_manifest", lambda _root: None)
    monkeypatch.setattr(install.time, "time", lambda: 100)
    if not first_install:
        install.install_project("fct_x", {asset: "# old"}, str(tmp_path))
    reads = 0
    restarted = False

    def post(url, **kwargs):
        nonlocal reads, restarted
        query = kwargs["json"]["query"]
        if query == verify.WORKSPACE:
            reads += 1
            if reads in (4, 5, 6):
                raise httpx.ConnectError("restart")
            response = dagster(url, **kwargs)
            body = response.json()
            entries = body["data"]["workspaceOrError"]["locationEntries"]
            if reads <= 3:
                if first_install:
                    entries[:] = [entry for entry in entries if entry["name"] != "fct_x"]
                else:
                    entries[0]["updatedTimestamp"] = 99
            else:
                restarted = True
            return httpx.Response(200, json=body, request=response.request)
        # Tiến trình cũ vẫn nhận launch, nhưng run sẽ bị giết khi watcher restart.
        if query in (verify.RELOAD, verify.LAUNCH) and not restarted:
            dagster.statuses = ["CANCELED"]
        return dagster(url, **kwargs)

    monkeypatch.setattr(verify.httpx, "post", post)
    before = verify.WORKSPACE_UPDATED_AFTER.get()

    def confirm(_target):
        assert verify.WORKSPACE_UPDATED_AFTER.get() == 100
        assert (
            verify.verify_pipeline("http://dagster:3070", "fct_x", 20, sleep=clock.sleep, clock=clock)
            == "dagster-run-1"
        )

    target = install.install_project("fct_x", {asset: "# new"}, str(tmp_path), verify=confirm)

    assert reads == 7 and clock.now == 12 and len(dagster.launches) == 1
    assert [body["query"] for _, body, _ in dagster.calls] == [*[verify.WORKSPACE] * 4, verify.LAUNCH, verify.STATUS]
    assert (target / asset).read_text(encoding="utf-8") == "# new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fct_x", "workspace.yaml"]
    assert verify.WORKSPACE_UPDATED_AFTER.get() == before


@pytest.mark.parametrize("stamp", [99, None, "100", True, float("nan"), float("inf")])
def test_stale_or_invalid_workspace_timestamp_never_launches(monkeypatch, stamp):
    dagster, clock = Dagster(), Clock()

    def post(url, **kwargs):
        data = dagster(
            url, json={"query": kwargs["query"], "variables": kwargs["variables"]}, timeout=kwargs["timeout"]
        ).json()["data"]
        data["workspaceOrError"]["locationEntries"][0]["updatedTimestamp"] = stamp
        return data

    monkeypatch.setattr(
        verify,
        "graphql",
        lambda url, query, variables, timeout: post(url, query=query, variables=variables, timeout=timeout),
    )
    context = verify.WORKSPACE_UPDATED_AFTER.set(100)
    try:
        with pytest.raises(verify.VerificationError, match="quá 5 giây"):
            verify.verify_pipeline("http://dagster:3070", "fct_x", 5, sleep=clock.sleep, clock=clock)
    finally:
        verify.WORKSPACE_UPDATED_AFTER.reset(context)

    assert clock.now == 5 and dagster.launches == []
    assert all(body["query"] == verify.WORKSPACE for _, body, _ in dagster.calls)


def test_location_load_error_fails_immediately_without_launch(monkeypatch):
    dagster, clock = Dagster(), Clock()

    def post(url, **kwargs):
        response = dagster(url, **kwargs)
        body = response.json()
        body["data"]["workspaceOrError"]["locationEntries"][0]["locationOrLoadError"] = {
            "__typename": "PythonError",
            "message": "secret-url",
        }
        return httpx.Response(200, json=body, request=response.request)

    monkeypatch.setattr(verify.httpx, "post", post)
    context = verify.WORKSPACE_UPDATED_AFTER.set(100)
    try:
        with pytest.raises(verify.VerificationError, match="fct_x.*PythonError") as exc:
            verify.verify_pipeline("http://dagster:3070", "fct_x", 120, sleep=clock.sleep, clock=clock)
    finally:
        verify.WORKSPACE_UPDATED_AFTER.reset(context)

    assert "secret-url" not in str(exc.value)
    assert clock.now == 0 and len(dagster.calls) == 1 and dagster.launches == []


def test_fresh_workspace_still_waits_until_location_is_loaded(monkeypatch):
    dagster, clock = Dagster(), Clock()
    reads = 0

    def post(url, **kwargs):
        nonlocal reads
        response = dagster(url, **kwargs)
        if kwargs["json"]["query"] == verify.WORKSPACE:
            reads += 1
            body = response.json()
            if reads < 3:
                body["data"]["workspaceOrError"]["locationEntries"][0]["loadStatus"] = "LOADING"
            response = httpx.Response(200, json=body, request=response.request)
        if kwargs["json"]["query"] == verify.LAUNCH:
            assert reads == 3
        return response

    monkeypatch.setattr(verify.httpx, "post", post)
    context = verify.WORKSPACE_UPDATED_AFTER.set(0)
    try:
        assert (
            verify.verify_pipeline("http://dagster:3070", "fct_x", 12, sleep=clock.sleep, clock=clock)
            == "dagster-run-1"
        )
    finally:
        verify.WORKSPACE_UPDATED_AFTER.reset(context)

    assert clock.now == 4 and len(dagster.launches) == 1


def test_failed_install_resets_workspace_timestamp_context(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "prepare_project", lambda root, files: root.mkdir())
    monkeypatch.setattr(install, "_compile_manifest", lambda _root: None)
    before = verify.WORKSPACE_UPDATED_AFTER.get()

    def fail(_target):
        assert verify.WORKSPACE_UPDATED_AFTER.get() is not None
        raise verify.VerificationError("Chạy thử lỗi.")

    with pytest.raises(verify.VerificationError, match="Chạy thử lỗi"):
        install.install_project("fct_x", {}, str(tmp_path), verify=fail)

    assert verify.WORKSPACE_UPDATED_AFTER.get() == before
    assert not (tmp_path / "fct_x").exists()
    assert (tmp_path / "workspace.yaml").read_text(encoding="utf-8").splitlines()[-1] == "load_from: []"


@pytest.mark.parametrize("kind", sorted(verify.LAUNCH_ERRORS))
def test_launch_rejection_fails_immediately_with_error_name(monkeypatch, kind):
    dagster, clock = Dagster(), Clock()
    dagster.error_query = verify.LAUNCH
    dagster.error = {"data": {"launchPipelineExecution": {"__typename": kind, "message": "secret-url"}}}
    monkeypatch.setattr(verify.httpx, "post", dagster)

    with pytest.raises(verify.VerificationError, match=kind) as exc:
        verify.verify_pipeline("http://dagster:3070", "fct_x", 120, sleep=clock.sleep, clock=clock)

    assert "secret-url" not in str(exc.value)
    assert clock.now == 0 and [body["query"] for _, body, _ in dagster.calls] == [verify.RELOAD, verify.LAUNCH]


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
    from graphql import get_named_type, parse, validate

    schema = create_schema().graphql_schema
    launch_type = get_named_type(schema.mutation_type.fields["launchPipelineExecution"].type)
    errors = {kind.name for kind in launch_type.types if kind.name != "LaunchRunSuccess"}
    assert errors <= verify.LAUNCH_ERRORS
    for query in (verify.WORKSPACE, verify.RELOAD, verify.LAUNCH, verify.STATUS, verify.FIND_RUN, verify.TERMINATE):
        assert validate(schema, parse(query)) == []
