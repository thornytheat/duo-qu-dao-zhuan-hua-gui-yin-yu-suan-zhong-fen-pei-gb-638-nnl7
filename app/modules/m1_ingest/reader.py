"""流式读取：保留物理行号、识别重复表头、末行截断、空行。

用 csv 模块逐条解析，引号内含分隔符或换行都不会把一条记录拆成两条。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field


@dataclass
class Row:
    row_no: int                 # 数据序号（从 1 开始，不含表头）
    raw_line: int               # 物理行号（csv 解析到的最后一行）
    fields: list[str]
    kind: str = "data"          # data | blank | mismatch | dup_header | truncated
    notes: list[str] = field(default_factory=list)


def _clean(fields: list[str]) -> list[str]:
    return [item.strip().lstrip("\ufeff") for item in fields]


def _classify(row: Row, expected: int, header: list[str], tail_missing: bool) -> Row:
    fields = _clean(row.fields)
    row.fields = fields
    if not fields or all(not item for item in fields):
        row.kind = "blank"
        return row
    if header and fields == header:
        row.kind = "dup_header"
        row.notes.append("重复表头")
        return row
    if len(fields) != expected:
        row.kind = "truncated" if (tail_missing and len(fields) < expected) else "mismatch"
        return row
    return row


def iter_rows(text: str, expected: int, header: list[str] | None = None):
    """逐条产出 Row；只在内存里留一条待判定记录。"""
    header = _clean(header) if header else []
    reader = csv.reader(io.StringIO(text, newline=""))
    tail_complete = text.endswith(("\n", "\r"))
    pending: Row | None = None
    row_no = 0
    for fields in reader:
        row_no += 1
        current = Row(row_no=row_no, raw_line=reader.line_num, fields=fields)
        if pending is not None:
            yield _classify(pending, expected, header, False)
        pending = current
    if pending is not None:
        yield _classify(pending, expected, header, not tail_complete)


def read_all(text: str, expected: int) -> dict:
    """一次读完一个小文件：第一条非空行当表头，其余为数据行。

    重复出现的表头行单独标记，不计入数据行，也不静默丢弃。
    """
    header: list[str] = []
    body: list[Row] = []
    for row in iter_rows(text, expected):
        if not header:
            if row.fields and not all(not item for item in row.fields):
                header = row.fields
                continue
            body.append(row)
            continue
        if row.kind == "blank":
            body.append(row)
            continue
        if row.fields == header:
            row.kind = "dup_header"
            row.notes.append("重复表头")
            body.append(row)
            continue
        body.append(row)
    counter = 0
    for row in body:
        if row.kind in {"blank", "dup_header"}:
            row.row_no = 0
            continue
        counter += 1
        row.row_no = counter
    return {"header": header, "rows": body, "header_only": bool(header) and not body}
