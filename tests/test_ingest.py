"""M1 触点接入：编码回退、BOM、重复表头、列数、截断、空态。"""
from __future__ import annotations

from app.modules.m1_ingest import service

from fixtures.expected import CLEAN, DIRTY


def test_dirty_files_use_bom_and_gb18030_fallback(dirty):
    files = dirty["stats"]["files"]
    # TRAP-02：UTF-8 BOM 由 utf-8-sig 吃掉，首列名不带 \ufeff
    assert files["touch"]["encoding"] == "utf-8-sig"
    assert files["touch"]["bom"] is True
    # TRAP-01：GB18030 中文表头走回退链，不崩、不乱码
    assert files["alias"]["encoding"] == "gb18030"


def test_first_column_name_is_clean(store, run_id):
    first = store.touchpoints(run_id)[0]
    assert first["touch_id"] == "TP-1001"
    assert "\ufeff" not in first["touch_id"]


def test_duplicate_header_and_blank_line_are_not_data_rows(dirty):
    files = dirty["stats"]["files"]
    # TRAP-04：文件中段的重复表头被识别，不当数据行
    assert files["touch"]["dup_headers"] == 1
    assert files["touch"]["blanks"] == 1
    assert files["touch"]["rows"] == DIRTY["touch_input"]


def test_row_with_extra_column_is_quarantined(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-05：多一列的行不静默补空，进隔离区并带码
    assert rows["TP-1026"]["status"] == "DROPPED"
    assert rows["TP-1026"]["reason"] == "TD_COL_COUNT"


def test_thousands_and_fullwidth_amounts_parse(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-06：千分位与全角数字都归一到分
    assert rows["TP-1008"]["cost_cents"] == 123450
    assert rows["TP-1027"]["cost_cents"] == 18000


def test_leading_zero_touch_id_kept_as_string(store, run_id):
    ids = [row["touch_id"] for row in store.touchpoints(run_id)]
    # TRAP-08：带前导零的编号保持字符串
    assert "TP-0016" in ids


def test_truncated_tail_line_is_rejected(store, run_id, dirty):
    # TRAP-16：末行没有换行且字段不足，半行被拒绝而不是当有效数据
    assert dirty["stats"]["files"]["touch"]["status"] == "truncated"
    tail = [row for row in store.touchpoints(run_id) if row["touch_id"] == "TP-1029"]
    assert len(tail) == 1
    assert tail[0]["reason"] == "TD_COL_COUNT"


def test_quoted_comma_keeps_one_record_per_line(store, run_id):
    rows = {row["touch_id"]: row for row in store.touchpoints(run_id)}
    # TRAP-03：带引号逗号的金额没有被拆成两条记录
    assert rows["TP-1008"]["raw_line"] - rows["TP-1007"]["raw_line"] == 1


def test_clean_dataset_has_no_quarantine(settings, store):
    result = service.run(settings, store, "clean")
    accounts = result["stats"]["accounts"]
    assert accounts["touch_counted"] == CLEAN["touch_counted"]
    assert accounts["touch_quarantined"] == CLEAN["quarantine"]
    assert result["stats"]["gmv_cents"] == CLEAN["gmv_cents"]


def test_three_empty_states_are_distinct(settings, store):
    # TRAP-18：0 行 / 仅表头 / 全空行三种空态给出不同结论
    codes = {dataset: service.run(settings, store, dataset)["status"]
             for dataset in ("empty", "header_only", "blank")}
    assert codes == {"empty": "empty", "header_only": "header_only",
                     "blank": "blank_rows"}
    assert store.latest_run()["stats"]["accounts"]["touch_input"] == 0


def test_unknown_dataset_returns_404(client):
    response = client.post("/api/import", json={"dataset": "nope"})
    assert response.status_code == 404
    assert response.json()["error"] == "E_DATASET_NOT_FOUND"
    assert "dirty" in response.json()["allowed"]
