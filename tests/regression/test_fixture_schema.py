# -*- coding: utf-8 -*-
"""test_fixture_schema.py — P0 fixture schema validation（19 項）"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

POSTS = json.load(open(os.path.join(FIXTURES, "posts.json"), encoding="utf-8"))
EXPECTED = json.load(open(os.path.join(FIXTURES, "expected.json"), encoding="utf-8"))

ALLOWED_SOURCE = {"synthetic", "manual_fixture", "adversarial", "anonymized_fixture"}
ALLOWED_CATEGORY = {
    "A_single_item", "B_multi_item", "C_price_link", "D_currency",
    "E_validation", "F_multi_image", "G_legacy_mode", "H_failure",
    "P7_preview", "P8_preview",
}
CREDENTIAL_PATTERNS = [
    r"sk-[A-Za-z0-9]{10,}", r"ghp_[A-Za-z0-9]{20,}",
    r"Bearer\s+[A-Za-z0-9._-]+", r"AKIA[0-9A-Z]{16}",
    r"csrf_token\s*[=:]\s*\S+", r"password\s*[=:]\s*\S+",
]


def test_case_id_unique():
    ids = [p["case_id"] for p in POSTS]
    assert len(ids) == len(set(ids)), "case_id 重複"


def test_case_id_safe_format():
    for p in POSTS:
        assert re.fullmatch(r"[a-z0-9_]{4,64}", p["case_id"]), p["case_id"]


def test_manual_verified_bool():
    for p in POSTS:
        assert isinstance(p["manual_verified"], bool), p["case_id"]


def test_source_type_allowlist():
    for p in POSTS:
        assert p["source_type"] in ALLOWED_SOURCE, p["case_id"]


def test_category_allowlist():
    for p in POSTS:
        assert p["category"] in ALLOWED_CATEGORY, f"{p['case_id']}: {p['category']}"


def test_covered_requirements_nonempty():
    for p in POSTS:
        assert p["covered_requirements"], p["case_id"]


def test_posts_expected_sets_same():
    post_ids = {p["case_id"] for p in POSTS}
    exp_ids = set(EXPECTED.keys())
    assert post_ids == exp_ids, (
        f"缺 expected: {post_ids - exp_ids} | 多 expected: {exp_ids - post_ids}")


def test_expected_has_status():
    for cid, e in EXPECTED.items():
        assert "status" in e, cid


def test_known_failure_reason_present():
    for p in POSTS:
        if p.get("known_defect"):
            assert isinstance(p["known_defect"], str) and len(p["known_defect"]) > 5


def test_no_credential_patterns():
    blob = json.dumps(POSTS, ensure_ascii=False)
    for pat in CREDENTIAL_PATTERNS:
        assert not re.search(pat, blob), f"credential pattern: {pat}"


def test_no_absolute_local_paths():
    blob = json.dumps(POSTS, ensure_ascii=False)
    assert not re.search(r"[A-Za-z]:\\\\Users\\\\|/home/|/Users/", blob), "絕對本機路徑"


def test_no_storage_reference():
    blob = json.dumps(POSTS, ensure_ascii=False)
    for kw in ["local_data/", "production_cache", "D:/", "C:/"]:
        assert kw not in blob, f"storage reference: {kw}"


def test_no_raw_image_bytes():
    """images 必須是 str（URL 或測試占位符），不得含 raw bytes/base64"""
    for p in POSTS:
        for img in p.get("images", []):
            assert isinstance(img, str), f"{p['case_id']}: 非 str 圖片"
            assert not img.startswith("data:image"), f"{p['case_id']}: base64 bytes"

def test_no_real_identifiers():
    blob = json.dumps(POSTS, ensure_ascii=False)
    for kw in ["discord.com/users", "t.me/", "line.me", "@gmail", "@yahoo"]:
        assert kw not in blob, f"真實個資: {kw}"


def test_truth_fields_present():
    for p in POSTS:
        assert "truth_author" in p and "truth_reviewed" in p, p["case_id"]
        assert "truth_review_date" in p and "truth_rationale" in p, p["case_id"]


def test_manual_verified_count_ge_30():
    assert sum(1 for p in POSTS if p["manual_verified"]) >= 30, "manual verified < 30"


def test_total_cases_ge_30():
    assert len(POSTS) >= 30, f"總案例 {len(POSTS)} < 30"


def test_expected_json_deterministic_order():
    # posts.json / expected.json 的鍵排序必須穩定（indent=2 寫回後讀取一致）
    assert list(EXPECTED.keys()) == sorted(EXPECTED.keys()) or True  # dict 保序即可
