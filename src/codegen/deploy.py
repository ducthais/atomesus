"""P16 deploy (SPEC §9.5): mở pull request chứa đúng bộ file Reviewer đã duyệt vào repo pipeline.

Nhánh `pipeline/<model_name>/<8 ký tự đầu run_id>-v<version>` tạo từ nhánh mặc định; một commit ghi
`pipelines/<model_name>/` gồm 4 + N file đã duyệt, `run_report.json` và `manifest.json` (src/deployer/manifest.py).
Backend chỉ mở pull request: CI của repo pipeline kiểm manifest, người có quyền merge, service deployer cài lên Dagster production (src/deployer/).
Gọi lại khi pull request đã mở thì trả pull request cũ; nhánh đã có mà chưa có pull request thì mở trên nhánh đó.
Mọi lần dùng lại nhánh có sẵn đều kiểm trước diff của nhánh so với nhánh mặc định chỉ gồm đúng bộ file đã ký của
chính run này (file thường, nội dung khớp, không gì ngoài thư mục pipeline); pull request
trả về phải trỏ đúng commit đã kiểm (hoặc vừa tạo), nhánh bị đổi giữa chừng thì ra ManifestError.
"""

import json
from datetime import UTC, datetime

from pydantic import ValidationError

from src.codegen.github import COMPARE_FILE_LIMIT, REGULAR_FILE, GithubClient, GithubError, blob_sha
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


def _deployment(github: GithubClient, branch: str, pull: dict, head_sha: str, base: str) -> Deployment:
    """Chỉ ghi nhận pull request khi đầu nhánh của nó đúng là commit vừa kiểm (hoặc vừa tạo) cho chữ ký duyệt, và nó
    merge vào nhánh mặc định (nơi deployer kéo code): pull request bị đổi đích sang nhánh khác không đưa được pipeline
    lên production."""
    if pull["head"]["sha"] != head_sha:
        raise ManifestError(
            f"Nhánh {branch} trên repo pipeline vừa bị đổi trong lúc mở pull request nên không còn chắc là bộ file đã "
            "duyệt. Kiểm tra pull request đó trên repo pipeline rồi Deploy lại."
        )
    if pull["base"]["ref"] != base:
        raise ManifestError(
            f"Pull request #{pull['number']} của nhánh {branch} đang nhắm vào nhánh {pull['base']['ref']} thay vì "
            f"nhánh mặc định {base}. Đổi lại đích của pull request về {base} (hoặc đóng nó) rồi Deploy lại."
        )
    return Deployment(
        repo=github.repo,
        branch=branch,
        pr_number=pull["number"],
        pr_url=pull["html_url"],
        head_sha=head_sha,
        state="open",
        opened_at=pull.get("created_at") or datetime.now(UTC),
    )


def _verify_branch(
    report: RunReport,
    files: dict[str, str],
    manifest: dict,
    base: str,
    branch: str,
    head_sha: str,
    github: GithubClient,
) -> None:
    """Nhánh đã có chỉ được dùng lại khi đầu nhánh chứa đúng bộ file đã ký của chính run này.

    Phần diff so với nhánh mặc định (đúng nội dung pull request sẽ merge) chỉ được nằm trong thư mục pipeline; thư mục
    đó ở đầu nhánh phải có đúng bộ file đã ký + run_report.json + manifest.json, mọi entry là file thường 100644 (không
    symlink, submodule hay file thực thi) và nội dung khớp. Tên nhánh chỉ lấy 8 ký tự đầu run_id nên vẫn có thể trùng
    với run khác; nhánh của run khác, của chữ ký khác hay bị sửa sau khi mở đều ra ManifestError (409), không bao giờ
    trả pull request của nó cho run này.
    """
    folder = project_dir(manifest["model_name"])
    changed = github.compare_files(base, head_sha)
    entries = {
        path.removeprefix(f"{folder}/"): entry
        for path, entry in github.tree_entries(github.commit_tree(head_sha)).items()
        if path.startswith(f"{folder}/")
    }
    matches = (
        len(changed) < COMPARE_FILE_LIMIT
        and all(path.startswith(f"{folder}/") for path in changed)
        and set(entries) == {*files, REPORT_FILE, MANIFEST_FILE}
        and all(entry["type"] == "blob" and entry["mode"] == REGULAR_FILE for entry in entries.values())
        and all(entries[path]["sha"] == blob_sha(text) for path, text in files.items())
    )
    if matches:
        try:
            saved_manifest = json.loads(github.blob_text(entries[MANIFEST_FILE]["sha"]))
            saved_report = json.loads(github.blob_text(entries[REPORT_FILE]["sha"]))
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
) -> str:
    """Một commit trên nhánh mới: thư mục pipeline chứa đúng bộ file đã duyệt (file cũ không còn trong bộ bị xóa).

    Trả sha đầu nhánh đã biết chắc chứa bộ file đó.
    """
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
    # Mọi entry cũ trong thư mục không thuộc bộ đã ký đều bị xóa, kể cả symlink và submodule: commit chỉ chứa đúng bộ đó.
    stale = {
        path: entry
        for path, entry in github.tree_entries(base_tree).items()
        if path.startswith(f"{folder}/") and path not in content
    }
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
        _verify_branch(report, files, manifest, base, branch, head_sha, github)
        return head_sha
    return commit


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
            head_sha = _commit_branch(report, files, manifest, base, branch, github)
        else:
            _verify_branch(report, files, manifest, base, branch, head_sha, github)
            if opened := github.open_pull_requests(branch):
                return _deployment(github, branch, opened[0], head_sha, base)
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
        return _deployment(github, branch, pull, head_sha, base)
    except (KeyError, TypeError, IndexError, ValidationError) as exc:
        raise GithubError(502, f"GitHub trả dữ liệu không đúng định dạng ({type(exc).__name__}).") from None
