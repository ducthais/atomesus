"""File nguồn riêng của Engineer: kiểm nội dung, chuyển XLSX thành CSV và chọn bảng như Postgres."""

import csv
import io
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from openpyxl import load_workbook
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from src.pipeline import files, store
from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import effective_policy, load_policy

router = APIRouter(prefix="/uploads", tags=["uploads"])


def engineer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "Engineer":
        raise HTTPException(403, "Chỉ Engineer được tải file, chọn bảng hoặc xóa file nguồn.")
    return user


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[str] = Field(min_length=1)

    @field_validator("tables")
    @classmethod
    def _names(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) for name in value):
            raise ValueError("Chọn ít nhất một tên bảng hợp lệ, không trùng.")
        return value


def table_name(value: str, used: set[str]) -> str:
    text = unicodedata.normalize("NFKD", value.replace("đ", "d").replace("Đ", "D"))
    text = text.encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_") or "table"
    if text[0].isdigit():
        text = f"t_{text}"
    if text in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
        text = f"t_{text}"
    text = text[:55]
    name, suffix = text, 2
    while name in used:
        name, suffix = f"{text}_{suffix}", suffix + 1
    used.add(name)
    return name


def upload_directory(owner_id: str, upload_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner_id) or not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        raise HTTPException(422, "Định danh file nguồn không hợp lệ.")
    base = files.PROJECT_ROOT.resolve() / "data" / "uploads"
    target = (base / owner_id / upload_id).resolve()
    if target != base / owner_id / upload_id:
        raise HTTPException(422, "Thư mục file nguồn không được trỏ ra ngoài vùng upload.")
    return target


def owned_upload(config: PipelineSettings, upload_id: str, owner_id: str) -> dict:
    metadata = store.get_upload(config.store_path, upload_id, owner_id)
    if metadata is None:
        raise HTTPException(404, "Không tìm thấy file nguồn.")
    return metadata


def _header(row: list) -> list[str]:
    header = [str(cell).strip() if cell is not None else "" for cell in row]
    if not header or any(not cell or not cell.isprintable() for cell in header) or len(set(header)) != len(header):
        raise HTTPException(422, "File phải có dòng tiêu đề với tên cột không rỗng, không trùng.")
    return header


def _write_table(directory: Path, name: str, rows, max_bytes: int, pad_rows: bool = False) -> dict:
    iterator = iter(rows)
    first = list(next(iterator, []))
    if pad_rows:
        while first and first[-1] is None:
            first.pop()
    header = _header(first)
    folder = directory / name
    folder.mkdir()
    count, size = 0, 0
    with (folder / "data.csv").open("wb") as handle:
        for index, row in enumerate(_with_header(header, iterator)):
            row = list(row)
            if not row:
                continue
            if pad_rows and index > 0:
                if not any(cell is not None for cell in row):
                    continue
                while len(row) > len(header) and row[-1] is None:
                    row.pop()
                row += [None] * (len(header) - len(row))
            if len(row) != len(header):
                raise HTTPException(422, "Số ô trên mỗi dòng phải khớp với số cột trong tiêu đề.")
            values = ["" if cell is None else str(cell) for cell in row]
            if any(any(ord(char) < 32 and char not in "\r\n\t" for char in cell) for cell in values):
                raise HTTPException(422, "File chứa ký tự nhị phân hoặc ký tự điều khiển không hợp lệ.")
            buffer = io.StringIO(newline="")
            csv.writer(buffer).writerow(values)
            encoded = buffer.getvalue().encode("utf-8")
            size += len(encoded)
            if size > max_bytes:
                raise HTTPException(413, "CSV sau chuyển đổi vượt dung lượng cho phép trong budgets.yml.")
            handle.write(encoded)
            count += index > 0
    return {"name": name, "columns": header, "column_count": len(header), "row_count": count, "size_bytes": size}


def _with_header(header, rows):
    yield header
    yield from rows


def _convert(incoming: list[UploadFile], directory: Path, max_file: int, max_total: int) -> dict:
    used, tables, names = set(), [], []
    size = 0
    for item in incoming:
        # Không dùng tên từ multipart làm đường dẫn, kể cả tên dùng dấu gạch chéo của Windows.
        filename = (item.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(filename).suffix.lower()
        if suffix not in (".csv", ".xlsx"):
            raise HTTPException(422, "Chỉ nhận file CSV hoặc XLSX.")
        data = item.file.read(max_file + 1)
        if len(data) > max_file:
            raise HTTPException(413, "File vượt dung lượng tối đa trong budgets.yml.")
        names.append(filename[:120])
        before = sum(table["size_bytes"] for table in tables)
        if suffix == ".csv":
            try:
                text = data.decode("utf-8-sig")
                if text.lstrip().lower().startswith(("%pdf-", "<?xml", "<!doctype", "<html")):
                    raise HTTPException(422, "Nội dung file không phải CSV có dòng tiêu đề.")
                try:
                    document = json.loads(text)
                except ValueError:
                    document = None
                if isinstance(document, (dict, list)):
                    raise HTTPException(422, "Nội dung JSON không phải CSV có dòng tiêu đề.")
                rows = csv.reader(io.StringIO(text, newline=""), strict=True)
                tables.append(_write_table(directory, table_name(Path(filename).stem, used), rows, max_file))
            except (UnicodeError, csv.Error):
                raise HTTPException(422, "CSV phải đọc được bằng UTF-8/UTF-8-BOM và có cấu trúc hợp lệ.") from None
        else:
            try:
                with ZipFile(io.BytesIO(data)) as archive:
                    if sum(entry.file_size for entry in archive.infolist()) > max_total:
                        raise HTTPException(413, "XLSX sau giải nén vượt dung lượng cho phép trong budgets.yml.")
                workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(422, "Không mở được XLSX. Kiểm tra nội dung file Excel.") from None
            try:
                for sheet in workbook.worksheets:
                    sheet.reset_dimensions()
                    remaining = max_file - (sum(table["size_bytes"] for table in tables) - before)
                    tables.append(
                        _write_table(
                            directory,
                            table_name(sheet.title, used),
                            sheet.iter_rows(values_only=True),
                            remaining,
                            pad_rows=True,
                        )
                    )
            except HTTPException:
                raise
            except OSError:
                raise
            except Exception:
                raise HTTPException(422, "Không đọc được các sheet của XLSX hoặc dòng tiêu đề không hợp lệ.") from None
            finally:
                workbook.close()
        converted = sum(table["size_bytes"] for table in tables) - before
        if converted > max_file:
            raise HTTPException(413, "CSV của file sau chuyển đổi vượt dung lượng tối đa trong budgets.yml.")
        size += max(len(data), converted)
        if size > max_total:
            raise HTTPException(413, "Tổng dung lượng file vượt giới hạn của bạn trong budgets.yml.")
    if not tables:
        raise HTTPException(422, "File không có bảng dữ liệu.")
    return {"name": ", ".join(names)[:120], "files": names, "tables": tables, "size_bytes": size}


def _create(incoming: list[UploadFile], user: dict, config: PipelineSettings, max_file: int, max_total: int) -> dict:
    upload_id = uuid4().hex
    directory = upload_directory(user["id"], upload_id)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        metadata = {
            **_convert(incoming, directory, max_file, max_total),
            "id": upload_id,
            "owner_id": user["id"],
            "root": directory.relative_to(files.PROJECT_ROOT.resolve()).as_posix(),
            "created_at": time.time(),
        }
        store.save_upload(config.store_path, metadata, max_total)
        return {**metadata, "selected_tables": []}
    except BaseException:
        shutil.rmtree(directory)
        raise


@router.post("", status_code=201)
async def create_upload(request: Request, user: dict = Depends(engineer), config: PipelineSettings = Depends(settings)):
    limits = load_policy().budgets.uploads
    max_file, max_total = int(limits.max_file_mb * 1024**2), int(limits.max_total_mb_per_user * 1024**2)

    async def bounded_stream():
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            # Phần framing multipart không tính vào quota file; chặn cả request chunked trước khi spool vô hạn.
            if received > max_total + 1024**2:
                raise MultiPartException("Tổng dung lượng tải lên vượt giới hạn trong budgets.yml.")
            yield chunk

    try:
        form = await MultiPartParser(request.headers, bounded_stream(), max_fields=0).parse()
    except MultiPartException as exc:
        status = 413 if "vượt giới hạn" in exc.message else 422
        raise HTTPException(status, "Multipart không hợp lệ." if status == 422 else exc.message) from None
    try:
        incoming = [value for key, value in form.multi_items() if key == "files" and isinstance(value, UploadFile)]
        if not incoming or len(incoming) != len(form.multi_items()):
            raise HTTPException(422, "Chọn một hoặc nhiều file trong trường files.")
        if any(item.size is not None and item.size > max_file for item in incoming):
            raise HTTPException(413, "File vượt dung lượng tối đa trong budgets.yml.")
        if sum(item.size or 0 for item in incoming) > max_total:
            raise HTTPException(413, "Tổng dung lượng file vượt giới hạn của bạn trong budgets.yml.")
        return await run_in_threadpool(_create, incoming, user, config, max_file, max_total)
    except store.UploadQuotaError as exc:
        raise HTTPException(413, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Không lưu được file nguồn. Vui lòng thử lại sau.") from None
    finally:
        await form.close()


@router.get("")
def list_uploads(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    return store.list_uploads(config.store_path, user["id"])


@router.get("/limits")
def upload_limits(user: dict = Depends(current_user), config: PipelineSettings = Depends(settings)):
    limits = load_policy().budgets.uploads.model_dump()
    return {
        **limits,
        "used_bytes": sum(item["size_bytes"] for item in store.list_uploads(config.store_path, user["id"])),
    }


@router.put("/{upload_id}/selection")
def select_tables(
    upload_id: str,
    body: SelectionRequest,
    user: dict = Depends(engineer),
    config: PipelineSettings = Depends(settings),
):
    metadata = owned_upload(config, upload_id, user["id"])
    if not set(body.tables) <= {table["name"] for table in metadata["tables"]}:
        raise HTTPException(422, "Bảng đã chọn không tồn tại trong file nguồn.")
    if not store.select_upload_tables(config.store_path, upload_id, user["id"], body.tables):
        raise HTTPException(404, "Không tìm thấy file nguồn.")
    policy = effective_policy(config, owner_id=user["id"])
    for domain_name, domain in policy.domains.domains.items():
        for ref, decl in domain.sources.items():
            if decl.connection_ref == f"upload:{upload_id}":
                return {"tables": body.tables, "domain": domain_name, "source_ref": ref}
    raise HTTPException(409, "Không tạo được domain cho file nguồn. Vui lòng thử lại.")


@router.delete("/{upload_id}", status_code=204)
def remove_upload(upload_id: str, user: dict = Depends(engineer), config: PipelineSettings = Depends(settings)):
    owned_upload(config, upload_id, user["id"])
    directory = upload_directory(user["id"], upload_id)
    try:
        if not store.delete_upload(
            config.store_path, upload_id, user["id"], lambda: shutil.rmtree(directory) if directory.exists() else None
        ):
            raise HTTPException(404, "Không tìm thấy file nguồn.")
    except store.ConnectionInUseError as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Không xóa được file nguồn. Vui lòng thử lại sau.") from None
    return Response(status_code=204)
