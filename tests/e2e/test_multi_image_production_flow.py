"""
test_multi_image_production_flow.py — crawler→production E2E（Phase 6.3D）

synthetic post → crawler vision pipeline → extract_vision_inputs_from_post
→ parse_post_for_production → ProductionParseResult
全程 fake（downloader / analyzer / legacy / BUFF），不碰網路。
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import analyze_arbitrage as aa  # noqa: E402
from alkaid_cs2.integration.crawler_vision import (  # noqa: E402
    analyze_post_images,
    build_post_vision_fields,
)
from alkaid_cs2.integration.production_bridge import parse_post_for_production  # noqa: E402

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "crawler_vision")
FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {"红线": "Redline", "紅線": "Redline"}
WEAPON_MAP = {"AK-47": "AK-47"}
LEGACY_OK = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
             "seller_price": 5000, "confidence": "high"}


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def build_post(fixture_name, bad=None, timeout_bad=None):
    """fixture → crawler vision 欄位齊全的 synthetic post。"""
    data = load_fixture(fixture_name)
    post = dict(data["post"])
    urls = post["images"]
    per = list(zip(data["vision_responses"]["type"], data["vision_responses"]["items"]))
    by_bytes = {f"img{i}".encode(): per[i] for i in range(len(per))}

    def fake_analyzer(image_bytes, custom_prompt=None, retry=None):
        t, its = by_bytes.get(image_bytes, (None, []))
        if "判斷這張" in custom_prompt:
            return t
        return its

    dl_map = {u: f"img{i}".encode() for i, u in enumerate(urls)}
    bad_set = set(bad or [])
    timeout_set = set(timeout_bad or [])

    def downloader(url, timeout):
        if url in timeout_set:
            raise TimeoutError("timeout")
        if url in bad_set:
            raise ConnectionError("conn error")
        return dl_map[url]

    results = analyze_post_images(
        urls, download_image_func=downloader, analyze_image_func=fake_analyzer,
        max_images=5, timeout_seconds=20)
    fields = build_post_vision_fields(results)
    post["vision_inputs"] = fields["vision_inputs"]
    post["vision_payloads"] = fields["vision_payloads"]
    if fields["legacy_items"]:
        post["items"] = fields["legacy_items"]
    if fields["currency"]:
        post["currency"] = fields["currency"]
    return post


def run_production(post, mode, legacy=None):
    vis = aa.extract_vision_inputs_from_post(post)
    if legacy is None:
        legacy = lambda t: dict(LEGACY_OK)  # noqa: E731
    return parse_post_for_production(
        post_id=post["id"], author=post["author"], link=post["url"],
        post_text=post["content"], image_urls=post["images"],
        vision_inputs=vis,
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP,
        legacy_parser=legacy, mode=mode,
    )


# ================================================================
# 1. shadow：legacy 正式 + Vision diff
# ================================================================
def test_multi_image_shadow_keeps_legacy():
    post = build_post("safe_multi_image_post.json")
    r = run_production(post, "shadow")
    assert r.source == "shadow_legacy"
    assert r.data == LEGACY_OK
    assert r.shadow_diff["vision_input_count"] == 2
    assert r.shadow_diff["vision_evidence_count"] == 2


# ================================================================
# 2. safe：多圖合併可用 V2
# ================================================================
def test_multi_image_safe_merged_v2():
    post = build_post("safe_multi_image_post.json")
    r = run_production(post, "safe")
    assert r.source == "v2"
    assert "vision_merged" in r.warnings
    assert r.data["seller_price"] == 5000


# ================================================================
# 3. 圖一安全圖二衝突 → fallback text V2
# ================================================================
def test_first_image_safe_second_conflict_falls_back_text():
    post = build_post("conflict_multi_image_post.json")
    r = run_production(post, "safe")
    assert r.source == "v2", "text V2 安全時不直接 legacy"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)
    assert r.data["seller_price"] == 5000


# ================================================================
# 4. 一失敗兩成功
# ================================================================
def test_one_failed_two_successful():
    post = build_post("three_images_all_success.json",
                      bad=["https://img/1.jpg"])
    r = run_production(post, "safe")
    vis = aa.extract_vision_inputs_from_post(post)
    assert len(vis) == 2, "兩張成功進入 V2"
    assert r.source == "v2", "text V2 安全 → 精確 v2（圖為中文名 image_only，fallback text）"


# ================================================================
# 5. 全部失敗 → text V2
# ================================================================
def test_all_failed_uses_text_v2():
    post = build_post("all_images_failed.json",
                      timeout_bad=["https://img/bad1.jpg", "https://img/bad2.jpg"])
    assert post["vision_inputs"] is None
    r = run_production(post, "safe")
    # 全部失敗 → vision_inputs=None → 走 text-only 路徑（source=v2 即 text V2 可用）
    assert r.source == "v2", "text-only V2 可用就不 fallback legacy"


# ================================================================
# 6. image-only 不安全
# ================================================================
def test_image_only_items_not_safe():
    post = build_post("three_images_all_success.json")
    post["content"] = "今天天氣很好"  # text 無商品 → 全為 image-only
    r = run_production(post, "safe")
    # Vision merge 不安全（image_only）且 text V2 不安全（無商品）→ legacy
    assert r.source == "legacy", "image-only 不放行 merged V2；text 無商品 → legacy"


# ================================================================
# 7. BUFF reference + text seller
# ================================================================
def test_buff_reference_plus_text_seller():
    post = build_post("mixed_image_kinds.json")
    post["content"] = "售 AK-47 | 红线 久经沙场 算5000"
    r = run_production(post, "safe")
    assert r.data is None or r.data.get("seller_price") == 5000, \
        "BUFF 掛牌價不得當 seller（seller 仍是 text 5000）"


# ================================================================
# 8. currency 衝突不安全
# ================================================================
def test_currency_conflict_not_safe():
    post = build_post("multi_currency.json")
    post["content"] = "售 紅線 算5000"
    r = run_production(post, "safe")
    # Vision 衝突（currency）→ text V2 安全 → 精確 v2（fallback text）
    assert r.source == "v2"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)
    assert r.data["seller_price"] == 5000, "RMB 不換算不當 seller"


# ================================================================
# 9. v2_only 衝突不 fallback legacy
# ================================================================
def test_v2_only_conflict_no_legacy():
    post = build_post("conflict_multi_image_post.json")
    calls = []

    def spy_legacy(text):
        calls.append(text)
        return dict(LEGACY_OK)

    r = run_production(post, "v2_only", legacy=spy_legacy)
    assert calls == [], "v2_only 永不呼叫 legacy"
    # Vision 衝突 → text V2 安全 → 精確 v2（fallback text）
    assert r.source == "v2"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)


# ================================================================
# Phase 6.3D.1 — 精確 fallback 斷言
# ================================================================
def test_safe_multi_image_exactly_v2():
    post = build_post("safe_multi_image_post.json")
    r = run_production(post, "safe")
    assert r.source == "v2", "安全多圖 merge 必須精確 source=v2"
    assert "vision_merged" in r.warnings
    assert r.data["seller_price"] == 5000


def test_conflict_falls_back_exactly_to_text_v2():
    post = build_post("conflict_multi_image_post.json")
    r = run_production(post, "safe")
    assert r.source == "v2", "conflict + text safe → 精確 text V2（不 legacy）"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)


def test_v2_only_both_unsafe_exactly_skipped():
    post = build_post("conflict_multi_image_post.json")
    post["content"] = "售 紅線 算5000 火神 算14000"  # text V2 也 AMBIGUOUS 不安全
    calls = []

    def spy_legacy(text):
        calls.append(text)
        return dict(LEGACY_OK)

    r = run_production(post, "v2_only", legacy=spy_legacy)
    assert r.source == "skipped", f"兩條路都不安全 → 精確 skipped: {r.source}"
    assert r.blocked is True
    assert r.data is None
    assert calls == [], "v2_only 永不呼叫 legacy"


def test_post_images_not_mutated():
    post = build_post("three_images_all_success.json")
    orig_id, orig_content = post["id"], post["content"]
    orig_images = list(post["images"])
    run_production(post, "safe")
    assert post["id"] == orig_id
    assert post["content"] == orig_content
    assert post["images"] == orig_images, "原始 images 不被修改"


# ================================================================
# 10. seller price 不重複換算
# ================================================================
def test_seller_price_not_double_converted():
    post = build_post("safe_multi_image_post.json")
    post["currency"] = "RMB"  # 模擬舊 currency 標記
    r = run_production(post, "safe")
    assert r.data["seller_price"] == 5000, "V2 結果不得 ×4.5"
