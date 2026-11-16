"""编码回退链：UTF-8（含 BOM）→ UTF-8 严格 → GB18030。

返回实际使用的编码，页面与清单都要能看到它，否则乱码无从复核。
"""
from __future__ import annotations

from pathlib import Path

CHAIN = ("utf-8-sig", "utf-8", "gb18030")


def read_text(path) -> dict:
    path = Path(path)
    raw = path.read_bytes()
    for encoding in CHAIN:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return {
            "text": text,
            "encoding": encoding,
            "bytes": len(raw),
            "bom": raw.startswith(b"\xef\xbb\xbf"),
        }
    return {"text": raw.decode("utf-8", errors="replace"), "encoding": "utf-8/replace",
            "bytes": len(raw), "bom": raw.startswith(b"\xef\xbb\xbf")}


def sniff_encoding(path) -> str:
    return read_text(path)["encoding"]
