"""跨设备别名合并：并查集，代表元取等价类里字典序最小的标识。

结果与输入行顺序无关；自引用与双向引用都不会改变代表元。
"""
from __future__ import annotations


class _Union:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        root = item
        while self.parent.get(root, root) != root:
            root = self.parent[root]
        while self.parent.get(item, item) != item:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: str, right: str) -> str:
        root_left, root_right = self.find(left), self.find(right)
        if root_left == root_right:
            return root_left
        keep, drop = sorted((root_left, root_right))
        self.parent[drop] = keep
        self.parent.setdefault(keep, keep)
        return keep


def resolve(rows: list[dict]) -> dict:
    """rows: [{alias_id, canonical_id, row_no, raw_line}]。

    返回 mapping（每个标识 → 代表元）、等价类、自引用与双向引用计数、坏行。
    """
    union = _Union()
    bad_rows, self_refs, pairs = [], [], set()
    for row in rows:
        left = (row.get("alias_id") or "").strip()
        right = (row.get("canonical_id") or "").strip()
        if not left or not right:
            bad_rows.append(row)
            continue
        if left == right:
            self_refs.append(left)
            union.parent.setdefault(left, left)
            continue
        pairs.add((left, right))
        union.union(left, right)
    for left, right in sorted(pairs):
        union.union(left, right)

    members: dict[str, list[str]] = {}
    for item in list(union.parent):
        members.setdefault(union.find(item), []).append(item)
    mapping: dict[str, str] = {}
    for root, group in members.items():
        representative = min(group)
        for item in group:
            mapping[item] = representative
    two_way = sorted({tuple(sorted((a, b))) for a, b in pairs if (b, a) in pairs})
    return {
        "mapping": mapping,
        "classes": [
            {"representative": min(group), "members": sorted(group)}
            for group in sorted(members.values(), key=lambda g: min(g))
        ],
        "self_refs": sorted(set(self_refs)),
        "two_way": [list(pair) for pair in two_way],
        "bad_rows": bad_rows,
        "resolved_count": len(mapping),
    }


def rep(mapping: dict[str, str], identifier: str | None) -> str | None:
    if identifier is None:
        return None
    text = str(identifier).strip()
    if not text:
        return None
    return mapping.get(text, text)
