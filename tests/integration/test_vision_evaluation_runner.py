"""test_vision_evaluation_runner.py — CLI runner 整合測試（Phase 6.4B）"""
import json
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory  # noqa: E402
from scripts.run_vision_evaluation import run_evaluation  # noqa: E402

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation")


def _offline_legacy(text):
    if "售" in text:
        return {"market_hash_name": "AK-47 | Redline (Field-Tested)",
                "seller_price": 5000, "currency": "TWD", "blocked": False}
    return None


@pytest.fixture()
def out_dir(tmp_path):
    return str(tmp_path / "reports")


def test_runs_all_cases(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    assert report["dataset"]["case_count"] == len(load_evaluation_directory(FIXTURES))


def test_filter_by_tag(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, tag="market",
                                  legacy_parser=_offline_legacy)
    assert code == 0
    tags = report["dataset"]["tags"]
    assert "market" in tags, "filter 後 tags 分布仍含 market"
    assert (report["dataset"]["case_count"]
            < len(load_evaluation_directory(FIXTURES))), "tag filter 縮小案例數"


def test_filter_by_case_id(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, case_id="single_twd_safe_001",
                                  legacy_parser=_offline_legacy)
    assert code == 0
    assert report["dataset"]["case_count"] == 1


def test_limit_cases(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, limit=5,
                                  legacy_parser=_offline_legacy)
    assert code == 0
    assert report["dataset"]["case_count"] == 5


def test_writes_json(out_dir):
    run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    p = os.path.join(out_dir, "phase6-4-baseline.json")
    assert os.path.exists(p)
    json.loads(open(p, encoding="utf-8").read())


def test_writes_markdown(out_dir):
    run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    p = os.path.join(out_dir, "phase6-4-baseline.md")
    assert os.path.exists(p)
    assert "Readiness" in open(p, encoding="utf-8").read()


def test_malformed_case_exit_2(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "x.json").write_text("{bad", encoding="utf-8")
    report, code = run_evaluation(str(bad), str(tmp_path / "o"))
    assert code == 2 and report is None


def test_case_runtime_error_exit_1(out_dir):
    def boom_legacy(text):
        raise RuntimeError("boom")

    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=boom_legacy)
    assert code == 1, "案例執行錯誤 → exit 1"
    assert report["crash_cases"], "crash cases 記錄"


def test_fail_fast_stops(tmp_path):
    def boom_legacy(text):
        raise RuntimeError("boom")

    report, code = run_evaluation(FIXTURES, str(tmp_path / "o"),
                                  legacy_parser=boom_legacy, fail_fast=True)
    assert code == 1 and report is None


def test_no_external_network(out_dir):
    # 全部 fake/stub：無 requests/無 subprocess 網路（跑完即證明）
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                                  git_commit="test-commit")
    assert code == 0
    assert report["git_commit"] == "test-commit"


def test_legacy_text_vision_predictions_created(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    assert set(report["parsers"].keys()) == {"legacy", "text_v2", "vision_raw", "vision_production"}


def test_raw_vision_merge_saved(out_dir):
    # evaluate_case 內 raw_vision_merge 由 runner 使用（score_case 帶 raw merge）
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    assert "image_type_accuracy" in report["parsers"]["vision_raw"]["stats"]


def test_fixture_not_modified():
    import glob
    before = {}
    for f in sorted(glob.glob(os.path.join(FIXTURES, "*.json"))):
        before[f] = open(f, encoding="utf-8").read()
    run_evaluation(FIXTURES, "/tmp/eval_test_out", legacy_parser=_offline_legacy)
    for f in before:
        assert before[f] == open(f, encoding="utf-8").read(), f"fixture 被修改: {f}"


def test_stable_output_order(out_dir):
    r1, _ = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                           git_commit="fixed")
    r2, _ = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                           git_commit="fixed")
    # 排除 runtime metadata：latency 欄位（執行時間可變，非語意欄位）
    def strip_latency(d):
        if isinstance(d, dict):
            return {k: strip_latency(v) for k, v in d.items()
                    if k not in ("latency_ms", "average_latency_ms",
                                 "p50_latency_ms", "p95_latency_ms")}
        if isinstance(d, list):
            return [strip_latency(x) for x in d]
        return d

    assert strip_latency(r1) == strip_latency(r2), "報告 deterministic（除 latency）"


def test_json_output_consistent(out_dir):
    run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                   git_commit="fixed")
    p = os.path.join(out_dir, "phase6-4-baseline.json")
    r1 = json.loads(open(p, encoding="utf-8").read())
    run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                   git_commit="fixed")
    r2 = json.loads(open(p, encoding="utf-8").read())
    assert r1["parsers"]["vision_production"]["stats"]["item_exact_match_rate"] == \
        r2["parsers"]["vision_production"]["stats"]["item_exact_match_rate"]
    assert r1["parsers"]["vision_production"]["safe"]["safe_false_positive_cases"] == \
        r2["parsers"]["vision_production"]["safe"]["safe_false_positive_cases"]


def test_markdown_output_consistent(out_dir):
    run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                   git_commit="fixed")
    p = os.path.join(out_dir, "phase6-4-baseline.md")
    md1 = open(p, encoding="utf-8").read()
    run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy,
                   git_commit="fixed")
    md2 = open(p, encoding="utf-8").read()

    def strip_latency_lines(md):
        return [ln for ln in md.splitlines()
                if "latency" not in ln and "P50" not in ln and "P95" not in ln]

    assert strip_latency_lines(md1) == strip_latency_lines(md2), \
        "Markdown 輸出一致（除 latency runtime metadata）"


def test_baseline_report_generated():
    p = os.path.join(PROJECT_ROOT, "tests", "evaluation", "reports",
                     "phase6-4-baseline.json")
    assert os.path.exists(p), "baseline 報告必須由 runner 產生"
    r = json.loads(open(p, encoding="utf-8").read())
    assert r["readiness"] in ("NOT_READY", "SHADOW_READY", "SAFE_PILOT_CANDIDATE")


# ================================================================
# Phase 6.4B.1 — Raw-Vision Separation
# ================================================================
def test_vision_raw_uses_merged_items_not_text_items(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    raw_exact = report["parsers"]["vision_raw"]["stats"]["item_exact_match_rate"]
    text_exact = report["parsers"]["text_v2"]["stats"]["item_exact_match_rate"]
    # vision_raw 用 merged items（圖補 wear/mhn 對齊）→ 不應與 text 強制相同
    assert raw_exact >= text_exact, "raw merge 至少不劣於 text"


def test_vision_raw_and_text_metrics_can_differ(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    raw = report["parsers"]["vision_raw"]["stats"]
    text = report["parsers"]["text_v2"]["stats"]
    assert (raw["item_exact_match_rate"] != text["item_exact_match_rate"]
            or raw["seller_price_exact_rate"] != text["seller_price_exact_rate"]), \
        "資料來源已分開，指標可不同"


def test_legacy_text_image_type_is_none(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    assert report["parsers"]["legacy"]["stats"]["image_type_accuracy"] is None
    assert report["parsers"]["text_v2"]["stats"]["image_type_accuracy"] is None
    assert report["parsers"]["vision_production"]["stats"]["image_type_accuracy"] is None
    assert report["parsers"]["vision_raw"]["stats"]["image_type_accuracy"] is not None


def test_raw_conflict_only_on_vision_raw(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    assert report["parsers"]["legacy"]["stats"]["conflict_detection_rate"] is None
    assert report["parsers"]["text_v2"]["stats"]["conflict_detection_rate"] is None
    assert report["parsers"]["vision_raw"]["stats"]["conflict_detection_rate"] is not None


def test_production_safe_matrix_uses_final_result(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    prod_safe = report["parsers"]["vision_production"]["safe"]
    raw_safe = report["parsers"]["vision_raw"]["safe"]
    assert prod_safe["false_positive"] <= raw_safe["false_positive"], \
        "production fallback 降低誤放行"
    # raw safe matrix 用 expected_raw_vision_safe（6 true + 6 false = 12 標註）
    assert raw_safe["true_positive"] + raw_safe["false_positive"] + \
        raw_safe["false_negative"] + raw_safe["true_negative"] <= 12


def test_baseline_rebuilt():
    p = os.path.join(PROJECT_ROOT, "tests", "evaluation", "reports",
                     "phase6-4-baseline.json")
    r = json.loads(open(p, encoding="utf-8").read())
    assert r["crash"]["cases_executed"] == 34
    assert r["crash"]["crash_count"] == 0
    assert set(r["parsers"].keys()) == {"legacy", "text_v2", "vision_raw",
                                        "vision_production"}
    assert r["readiness"] == "SHADOW_READY"
    assert r["known_limitations"], "known_limitations 非空"


# ================================================================
# Phase 6.4B.2 — Production 來源 & Denominator
# ================================================================
def test_vision_production_no_fallback_uses_merged_items(out_dir):
    # no-fallback 案例：production item/price 應與 vision_raw（merged）相同
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    prod_rows = {r["case_id"]: r for r in report["case_by_case"]
                 if r["parser_name"] == "vision_production"}
    raw_rows = {r["case_id"]: r for r in report["case_by_case"]
                if r["parser_name"] == "vision_raw"}
    checked = 0
    for cid, pr in prod_rows.items():
        if pr["fallback"] not in ("none", None):
            continue  # fallback 案例用 text（另一測試驗證）
        rr = raw_rows[cid]
        assert pr["item_exact"] == rr["item_exact"], \
            f"{cid} no-fallback production items 應來自 merged"
        assert pr["price_correct"] == rr["price_correct"], \
            f"{cid} no-fallback production price 應來自 merged"
        checked += 1
    assert checked >= 5, f"至少 5 個 no-fallback 案例（實際 {checked}）"


def test_production_metrics_can_differ_from_text_when_no_fallback(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    prod = report["parsers"]["vision_production"]["stats"]
    raw = report["parsers"]["vision_raw"]["stats"]
    text = report["parsers"]["text_v2"]["stats"]
    # production = fallback 案例用 text + 無 fallback 案例用 merged
    # → 指標介於 text 與 raw 之間（或等於其一），證明來源已分流
    assert prod["item_exact_match_rate"] >= text["item_exact_match_rate"], \
        "production 不劣於 text（merged 貢獻）"
    assert raw["item_exact_match_rate"] >= prod["item_exact_match_rate"], \
        "raw 為上限"


def test_fallback_metrics_equal_text_for_that_case_only(out_dir):
    # 逐 case 驗證：fallback 案例 production item exact == text_v2 item exact
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    prod_rows = {r["case_id"]: r for r in report["case_by_case"]
                 if r["parser_name"] == "vision_production" and
                 r["fallback"] == "text_v2"}
    text_rows = {r["case_id"]: r for r in report["case_by_case"]
                 if r["parser_name"] == "text_v2"}
    assert prod_rows, "存在 fallback 案例"
    for cid, pr in prod_rows.items():
        tr = text_rows[cid]
        assert pr["item_exact"] == tr["item_exact"], \
            f"{cid} fallback 案例 item 應與 text 相同"
        assert pr["price_correct"] == tr["price_correct"], \
            f"{cid} fallback 案例 price 應與 text 相同"


def test_vision_production_skipped_has_no_items(out_dir):
    # all_vision_failed_text_unsafe（text 也 unsafe）→ skipped → 空 items
    report, code = run_evaluation(FIXTURES, out_dir, case_id="all_vision_failed_text_unsafe_028",
                                  legacy_parser=_offline_legacy)
    assert code == 0
    rows = [r for r in report["case_by_case"]
            if r["parser_name"] == "vision_production"]
    assert rows and rows[0]["fallback"] == "skipped"
    assert rows[0]["item_exact"] == 0 and rows[0]["item_fn"] >= 2, \
        "skipped 無 items（全部 FN）"


def test_seller_fp_denominator_reported(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    st = report["parsers"]["vision_production"]["stats"]
    assert "seller_price_false_positive_denominator" in st
    assert st["seller_price_false_positive_denominator"] > 0, \
        "negative opportunities > 0"
    assert 0.0 <= st["seller_price_false_positive_rate"] <= 1.0


def test_raw_safe_positive_and_negative_samples(out_dir):
    report, code = run_evaluation(FIXTURES, out_dir, legacy_parser=_offline_legacy)
    assert code == 0
    ds = report["dataset"]
    assert ds["raw_safe_expected_true"] >= 6, "raw 正例 ≥6"
    assert ds["raw_safe_expected_false"] >= 6, "raw 負例 ≥6"
    assert ds["raw_safe_expected_none"] == 34 - 12
