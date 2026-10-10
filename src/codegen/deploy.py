"""P16 deploy (SPEC §9.5): mở pull request chứa đúng bộ file Reviewer đã duyệt vào repo pipeline.

Nhánh `pipeline/<model_name>/<8 ký tự đầu run_id>-v<version>` tạo từ nhánh mặc định; một commit ghi
`pipelines/<model_name>/` gồm 4 + N file đã duyệt, `run_report.json` và `manifest.json` (src/deployer/manifest.py).
Backend chỉ mở pull request: CI của repo pipeline kiểm manifest, người có quyền merge, service deployer cài lên Dagster production (src/deployer/).
Gọi lại khi pull request đã mở thì trả pull request cũ; nhánh đã có mà chưa có pull request thì mở trên nhánh đó.
Mọi lần dùng lại nhánh có sẵn đều kiểm trước nội dung đầu nhánh khớp chữ ký duyệt của chính run này.
"""

import json
from datetime import UTC, datetime

from pydantic import ValidationError

from src.codegen.github import GithubClient, GithubError, blob_sha
from src.deployer.manifest import MANIFEST_FILE, REPORT_FILE, ManifestError, build_manifest, project_dir
from src.run_report.models import Deployment, RunReport


def branch_name(model_name: str, run_id: str, version: int) -> str:
    # Version đánh số riêng trong từng run nên tên nhánh phải có run_id: hai run cùng model không trùng nhánh.
    return f"pipeline/{model_name}/{run_id[:8]}-v{version}"


def _body(report: RunReport, model_name: str, report_url: str) -> str:
    approval = report.approval
    lines = [
        f"Pipeline `{model_name}` version {report.version}, mở tự động sau khi Reviewer duyệt (SPEC §9.5).",
        "",
        f"- run_id: `{report.run_id}`",
        f"- files_hash: `{report.files_hash}`",
        f"- Chữ ký duyệt: `{approval.reviewer}` lúc {approval.at.isoformat()} (version {approval.version}, "
        f"files_hash `{approval.files_hash}`)",
    ]
    if approval.comment:
        lines.append(f"- Nhận xét của Reviewer: {approval.comment}")
    lines += [
        f"- RunReport: [{REPORT_FILE}]({report_url})",
        "",
        f"CI phải kiểm `{MANIFEST_FILE}` (sha256 từng file và files_hash khớp chữ ký duyệt) trước khi merge. "
        "Hệ thống không có quyền merge; người có quyền merge của repo quyết định.",
    ]
    return "\n".join(lines) + "\n"


def _deployment(github: GithubClient, branch: str, pull: dict) -> Deployment:
    return Deployment(
        repo=github.repo,
        branch=branch,
        pr_number=pull["number"],
        pr_url=pull["html_url"],
        head_sha=pull["head"]["sha"],
        state="open",
        opened_at=pull.get("created_at") or datetime.now(UTC),
    )


def _verify_branch(
    report: RunReport, files: dict[str, str], manifest: dict, branch: str, head_sha: str, github: GithubClient
) -> None:
    """Nhánh đã có chỉ được dùng lại khi đầu nhánh chứa đúng bộ file đã ký của chính run này.

    Tên nhánh chỉ lấy 8 ký tự đầu run_id nên vẫn có thể trùng với run khác; nhánh của run khác, của chữ ký khác hay bị
    sửa sau khi mở đều ra ManifestError (409), không bao giờ trả pull request của nó cho run này.
    """
    folder = project_dir(manifest["model_name"])
    blobs = {
        path.removeprefix(f"{folder}/"): sha
        for path, sha in github.tree_blobs(github.commit_tree(head_sha)).items()
        if path.startswith(f"{folder}/")
    }
    matches = set(blobs) == {*files, REPORT_FILE, MANIFEST_FILE} and all(
        blobs[path] == blob_sha(text) for path, text in files.items()
    )
    if matches:
        try:
            saved_manifest = json.loads(github.blob_text(blobs[MANIFEST_FILE]))
            saved_report = json.loads(github.blob_text(blobs[REPORT_FILE]))
        except ValueError:
            saved_manifest = saved_report = None
        signed = {
            "run_id": report.run_id,
            "version": report.version,
            "files_hash": report.files_hash,
            "approval": report.approval.model_dump(mode="json"),
        }
        matches = (
            saved_manifest == manifest
            and isinstance(saved_report, dict)
            and {key: saved_report.get(key) for key in signed} == signed
        )
    if not matches:
        raise ManifestError(
            f"Nhánh {branch} trên repo pipeline đã có nội dung khác bộ file đã duyệt của run này (có thể của một run "
            "khác cùng model và version). Đóng pull request và xóa nhánh đó trên repo pipeline rồi Deploy lại."
        )


def _commit_branch(
    report: RunReport, files: dict[str, str], manifest: dict, base: str, branch: str, github: GithubClient
) -> None:
    """Một commit trên nhánh mới: thư mục pipeline chứa đúng bộ file đã duyệt (file cũ không còn trong bộ bị xóa)."""
    base_sha = github.ref_sha(base)
    if base_sha is None:
        raise GithubError(
            502, f"Nhánh mặc định {base} của repo pipeline chưa có commit; tạo commit đầu tiên rồi Deploy lại."
        )
    base_tree = github.commit_tree(base_sha)
    folder = project_dir(manifest["model_name"])
    content = {f"{folder}/{path}": text for path, text in files.items()}
    content[f"{folder}/{REPORT_FILE}"] = report.model_dump_json(indent=2, exclude={"deployment"}) + "\n"
    content[f"{folder}/{MANIFEST_FILE}"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    stale = [path for path in github.tree_blobs(base_tree) if path.startswith(f"{folder}/") and path not in content]
    tree = github.create_tree(base_tree, content, delete=stale)
    message = (
        f"pipeline({manifest['model_name']}): v{report.version} đã duyệt\n\n"
        f"run_id: {report.run_id}\nfiles_hash: {report.files_hash}\nreviewer: {report.approval.reviewer}\n"
    )
    commit = github.create_commit(message, tree, [base_sha])
    try:
        github.create_ref(branch, commit)
    except GithubError as exc:
        # Request khác vừa tạo nhánh này: chỉ dùng khi đúng bộ file đã ký của run này. Commit vừa tạo không gắn ref
        # nào và GitHub tự dọn.
        head_sha = github.ref_sha(branch) if exc.upstream == 422 else None
        if head_sha is None:
            raise
        _verify_branch(report, files, manifest, branch, head_sha, github)


def open_pull_request(report: RunReport, files: dict[str, str], github: GithubClient) -> Deployment:
    """Mở (hoặc trả lại) pull request của bản đã duyệt. Lỗi GitHub ra GithubError; bộ file lệch chữ ký ra ManifestError."""
    approval = report.approval
    if report.status != "APPROVED" or approval is None or approval.decision != "approved":
        raise ManifestError("Run chưa được duyệt nên không mở pull request.")
    model_name = (report.spec or {}).get("transform", {}).get("model_name", "")
    manifest = build_manifest(
        run_id=report.run_id,
        version=report.version,
        model_name=model_name,
        files=files,
        reviewer=approval.reviewer,
        approved_at=approval.at,
    )
    signed = approval.files_hash == report.files_hash == manifest["files_hash"] and approval.version == report.version
    if not signed:
        raise ManifestError("Bộ file không khớp chữ ký duyệt nên không mở pull request.")
    branch = branch_name(model_name, report.run_id, report.version)
    try:
        repo = github.repository()
        base = repo["default_branch"]
        head_sha = github.ref_sha(branch)
        if head_sha is None:
            _commit_branch(report, files, manifest, base, branch, github)
        else:
            _verify_branch(report, files, manifest, branch, head_sha, github)
            if opened := github.open_pull_requests(branch):
                return _deployment(github, branch, opened[0])
        report_url = f"{repo['html_url']}/blob/{branch}/{project_dir(model_name)}/{REPORT_FILE}"
        title = f"Pipeline {model_name} v{report.version} (run {report.run_id[:8]})"
        try:
            pull = github.create_pull_request(
                title=title, body=_body(report, model_name, report_url), head=branch, base=base
            )
        except GithubError as exc:
            # Request Deploy khác vừa mở pull request cho nhánh này: trả lại pull request đó.
            opened = github.open_pull_requests(branch) if exc.upstream == 422 else []
            if not opened:
                raise
            pull = opened[0]
        return _deployment(github, branch, pull)
    except (KeyError, TypeError, IndexError, ValidationError) as exc:
        raise GithubError(502, f"GitHub trả dữ liệu không đúng định dạng ({type(exc).__name__}).") from None
