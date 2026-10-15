"""Xem và tải bảng gold của pipeline đã deploy (ADR-0001 D9). Chỉ đọc warehouse production, read-only.

Quyền theo chủ tên model (src.run_report.access.model_visible): chủ, Reviewer đã liên kết với chủ, Admin. Không được
thấy, tên model lạ hay bảng chưa có đều trả 404 cùng một thông báo, để không lộ pipeline của người khác có hay không.
"""

import csv
import io
import math
import re
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from src.pipeline.auth import current_user, settings
from src.pipeline.settings import PipelineSettings
from src.policy import PolicyError, load_policy
from src.run_report.access import model_visible

router = APIRouter(prefix="/results", tags=["results"], dependencies=[Depends(current_user)])

MODEL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")  # tên model dbt; mọi tên khác là 404
MAX_PAGE_SIZE = 200
LOCKED = "Pipeline đang chạy, thử lại sau ít phút."
CSV_CHUNK_ROWS = 1000
XLSX_MAX_CELL = 32767  # Excel không mở được ô dài hơn


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _not_found(model: str) -> HTTPException:
    return HTTPException(404, f"Không có kết quả gold.{model} mà bạn được xem.")


def _connect(model: str, user: dict, config: PipelineSettings) -> duckdb.DuckDBPyConnection:
    """Mở warehouse read-only sau khi đã xét quyền. Dagster đang ghi (giữ khóa file) → 503."""
    if not MODEL_NAME.match(model) or not model_visible(user, config.store_path, model):
        raise _not_found(model)
    warehouse = config.warehouse_for(model)  # file riêng của pipeline, bundle cũ thì file chung
    if not Path(warehouse).is_file():
        raise _not_found(model)
    try:
        return duckdb.connect(warehouse, read_only=True)
    except duckdb.Error as exc:
        if "lock" in str(exc).lower():
            raise HTTPException(503, LOCKED) from exc
        raise


def _columns(db: duckdb.DuckDBPyConnection, model: str) -> list[dict]:
    rows = db.execute(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'gold' AND table_name = ? ORDER BY ordinal_position",
        [model],
    ).fetchall()
    if not rows:
        raise _not_found(model)
    return [{"name": name, "type": kind} for name, kind in rows]


def _select(model: str, columns: list[dict], sort: str | None, order: str) -> str:
    """SELECT có thứ tự ổn định (cột sắp xếp trước, rồi mọi cột) để phân trang không lặp hay sót dòng.
    `sort` phải là tên cột có thật; tên bảng và cột đều được quote."""
    names = [c["name"] for c in columns]
    if sort is not None and sort not in names:
        raise HTTPException(422, f"Không có cột '{sort}' trong gold.{model} để sắp xếp.")
    keys = ([f"{_quote(sort)} {order.upper()} NULLS LAST"] if sort else []) + [_quote(n) for n in names]
    return f"SELECT * FROM gold.{_quote(model)} ORDER BY {', '.join(keys)}"  # noqa: S608


def _plain(value):
    """Giá trị đọc từ DuckDB → giá trị JSON / CSV / XLSX ghi được: bytes thành hex, số không hữu hạn thành chữ."""
    if isinstance(value, bytes | bytearray):
        return value.hex()
    if isinstance(value, float | Decimal) and not math.isfinite(value):
        return str(value)
    return value


@router.get("/{model}")
def read_results(
    model: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    sort: str | None = None,
    order: Literal["asc", "desc"] = "asc",
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> dict:
    """Một trang của gold.<model>: `rows` là list giá trị theo thứ tự `columns`."""
    with _connect(model, user, config) as db:
        columns = _columns(db, model)
        query = _select(model, columns, sort, order)
        total = db.execute(f"SELECT count(*) FROM gold.{_quote(model)}").fetchone()[0]  # noqa: S608
        rows = db.execute(f"{query} LIMIT {page_size} OFFSET {(page - 1) * page_size}").fetchall()
    return {
        "columns": columns,
        "rows": [[_plain(v) for v in row] for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _max_export_rows() -> int:
    try:
        return load_policy().budgets.results.max_export_rows
    except PolicyError as exc:
        raise HTTPException(500, str(exc)) from exc


def _csv(names: list[str], rows: list[tuple]) -> Iterator[bytes]:
    """CSV UTF-8 có BOM (Excel mới nhận ra tiếng Việt), ghi từng khúc."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    buffer.write("﻿")
    writer.writerow(names)
    for start in range(0, len(rows), CSV_CHUNK_ROWS):
        writer.writerows([_plain(v) for v in row] for row in rows[start : start + CSV_CHUNK_ROWS])
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate()
    if buffer.tell():
        yield buffer.getvalue().encode("utf-8")


_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _cell(ref: str, value) -> str:
    value = _plain(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int | float | Decimal):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, datetime | date | time):
        value = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    text = escape(_XML_INVALID.sub("", str(value))[:XLSX_MAX_CELL])
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


_XLSX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    ),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="xl/workbook.xml"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
        "</Relationships>"
    ),
    "xl/_rels/workbook.xml.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
        "</Relationships>"
    ),
}


def _xlsx(model: str, names: list[str], rows: list[tuple]) -> bytes:
    """Một sheet SpreadsheetML tối thiểu, chuỗi ghi inline (không cần sharedStrings). Viết tay bằng zipfile để không
    thêm thư viện; ngày giờ ghi dạng chữ ISO."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in _XLSX_PARTS.items():
            archive.writestr(path, content)
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(model[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        with archive.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(
                b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            )
            letters = [_column_letter(i) for i in range(len(names))]
            for number, row in enumerate([names, *rows], start=1):
                cells = "".join(_cell(f"{letters[i]}{number}", v) for i, v in enumerate(row))
                sheet.write(f'<row r="{number}">{cells}</row>'.encode())
            sheet.write(b"</sheetData></worksheet>")
    return output.getvalue()


@router.get("/{model}/export")
def export_results(
    model: str,
    file_format: Literal["csv", "xlsx"] = Query("csv", alias="format"),
    sort: str | None = None,
    order: Literal["asc", "desc"] = "asc",
    user: dict = Depends(current_user),
    config: PipelineSettings = Depends(settings),
) -> Response:
    """Toàn bộ gold.<model> thành <model>.csv hoặc <model>.xlsx. Quá trần results.max_export_rows → 413.

    Đọc hết vào bộ nhớ rồi mới đóng kết nối: không giữ khóa đọc warehouse trong lúc trình duyệt tải chậm."""
    limit = _max_export_rows()
    with _connect(model, user, config) as db:
        columns = _columns(db, model)
        query = _select(model, columns, sort, order)
        total = db.execute(f"SELECT count(*) FROM gold.{_quote(model)}").fetchone()[0]  # noqa: S608
        if total > limit:
            raise HTTPException(
                413,
                f"gold.{model} có {total} dòng, vượt trần xuất {limit} dòng (results.max_export_rows trong "
                "policy/budgets.yml). Hãy xem từng trang trên web hoặc nhờ Engineer xuất trực tiếp từ warehouse.",
            )
        rows = db.execute(query).fetchall()
    names = [c["name"] for c in columns]
    if file_format == "csv":
        return StreamingResponse(
            _csv(names, rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{model}.csv"'},
        )
    return Response(
        _xlsx(model, names, rows),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{model}.xlsx"'},
    )
