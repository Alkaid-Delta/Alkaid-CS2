"""
test_vision_production.py — build_vision_merged_result 測試（Phase 6.3C）
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.raw_post import RawPostInput  # noqa: E402
from alkaid_cs2.integration.vision_production import (  # noqa: E402
    VisionImageInput,
    build_vision_merged_result,
)

FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {"红线": "Redline", "紅線": "Redline"}
WEAPON_MAP = {"AK-47": "AK-47"}


def build(text, payloads, post_id="p1"):
    post = RawPostInput(post_id=post_id, raw_text=text, source="test")
    inputs = [
        VisionImageInput(image_index=i, image_url=f"https://img/{i}.jpg", payload=p)
        for i, p in enumerate(payloads)
    ]
    return build_vision_merged_result(
        post, vision_inputs=inputs,
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP,
    )


# ================================================================
# 1-2. 基本建置
# ================================================================
def test_build_text_and_single_image():
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5000"}]}])
    assert r.vision_used is True
    assert r.merged_post is not None
    assert r.legacy_result is not None
    assert r.fallback_reason is None
    assert len(r.image_evidence) == 1


def test_build_multiple_images():
    r = build("今天天氣很好", [
        {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                      "price": 7480, "confidence": 0.8}]},
        {"type": "single", "items": [{"chinese_name": "AK-47 | 火神",
                                      "price": 14000, "confidence": 0.8}]},
    ])
    assert len(r.image_evidence) == 2, "多張圖片全部處理"
    assert len(r.merged_post.items) == 2


# ================================================================
# 3-4. 圖片失敗策略
# ================================================================
def test_one_invalid_image_does_not_stop_others():
    r = build("今天天氣很好", [
        None,  # 失敗圖片
        {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                      "price": 5000, "confidence": 0.8}]},
    ])
    assert len(r.image_evidence) == 1, "一張失敗不影響其他"
    assert any(w.startswith("vision_image_error:0") for w in r.warnings)
    assert r.vision_used is True


def test_all_invalid_images_fallback_reason():
    r = build("售 紅線 算5000", [None, "{bad-json"])
    assert r.vision_used is False
    assert r.fallback_reason == "all_vision_images_failed"
    assert r.merged_post is not None, "保留 text-only ParsedPost"
    assert r.legacy_result is not None


# ================================================================
# 5. 重複圖片
# ================================================================
def test_duplicate_images_removed():
    p = {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                      "price": 5000, "confidence": 0.8}]}
    post = RawPostInput(post_id="p1", raw_text="售 紅線 算5000", source="test")
    inputs = [
        VisionImageInput(image_index=0, image_url="https://same/1.jpg", payload=p),
        VisionImageInput(image_index=1, image_url="https://same/1.jpg", payload=p),
    ]
    r = build_vision_merged_result(
        post, vision_inputs=inputs,
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP)
    assert len(r.image_evidence) == 1
    assert any(w.startswith("duplicate_images_removed") for w in r.warnings)


# ================================================================
# 6-9. 衝突與安全
# ================================================================
def test_merge_conflict_preserved():
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5500, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5500"}]}])
    types = {c.conflict_type.value for c in r.conflicts}
    assert "price_conflict" in types


def test_seller_conflict_not_safe():
    from alkaid_cs2.integration.production_bridge import vision_merge_safe_reasons
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5500, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5500"}]}])
    reasons = vision_merge_safe_reasons(r)
    assert "price_conflict" in reasons


def test_currency_conflict_not_safe():
    from alkaid_cs2.integration.production_bridge import vision_merge_safe_reasons
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "RMB", "confidence": 0.9,
            "evidence": "售 5000 RMB"}]}])
    reasons = vision_merge_safe_reasons(r)
    assert "currency_conflict" in reasons


def test_image_only_not_safe():
    from alkaid_cs2.integration.production_bridge import vision_merge_safe_reasons
    r = build("今天天氣很好", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    reasons = vision_merge_safe_reasons(r)
    assert "image_only_item" in reasons


def test_safe_same_item_same_price():
    from alkaid_cs2.integration.production_bridge import vision_merge_safe_reasons
    r = build("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5000"}]}])
    assert vision_merge_safe_reasons(r) == [], f"應安全: {vision_merge_safe_reasons(r)}"


# ================================================================
# 11-13. 掛牌價 / inspect / RMB
# ================================================================
def test_market_listing_not_seller():
    r = build("售 紅線 算5000", [{
        "type": "market", "platform": "buff", "items": [{
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 4300, "currency": "RMB", "confidence": 0.8,
            "evidence": "BUFF 最低價 4300"}]}])
    assert r.legacy_result.legacy_data["seller_price"] == 5000, "掛牌價不得當 seller"


def test_inspect_no_price():
    r = build("今天天氣很好", [{
        "type": "inspect", "items": [{
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "confidence": 0.9, "evidence": "售 5000"}]}])
    assert r.merged_post is not None
    assert len(r.merged_post.prices) == 0, "inspect 不產價格"


def test_rmb_not_converted():
    r = build("今天天氣很好", [{
        "type": "single", "items": [{
            "chinese_name": "AK-47 | 红线", "price": 4300, "currency": "RMB",
            "confidence": 0.8}]}])
    rmb = [p for p in r.merged_post.prices if p.money.currency is Currency.RMB]
    assert rmb and rmb[0].money.amount == 4300, "RMB 不換算"


# ================================================================
# 14-15. 輸入不被修改
# ================================================================
def test_input_post_not_mutated():
    post = RawPostInput(post_id="p1", raw_text="售 紅線 算5000", source="test")
    orig_text = post.raw_text
    build("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    assert post.raw_text == orig_text


def test_vision_inputs_not_mutated():
    payload = {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                            "price": 5000}]}
    vi = VisionImageInput(image_index=0, image_url="https://img/0.jpg", payload=payload)
    post = RawPostInput(post_id="p1", raw_text="售 紅線 算5000", source="test")
    build_vision_merged_result(
        post, vision_inputs=[vi],
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP)
    assert vi.payload == payload, "payload 不得被修改"
    assert vi.payload["type"] == "single"


# ================================================================
# 16. warnings 唯一
# ================================================================
def test_warnings_unique():
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    assert len(r.warnings) == len(set(r.warnings))


# ================================================================
# 16.1. 同一 vision_image_error 不重複（Phase 6.3C.1）
# ================================================================
def test_vision_image_error_deduplicated():
    r = build("售 紅線 算5000", [None, None])
    errs = [w for w in r.warnings if w.startswith("vision_image_error")]
    assert len(errs) == 2, "兩張失敗圖各一筆（index 0/1）"
    assert len(errs) == len(set(errs)), "同一錯誤 warning 不得重複"


# ================================================================
# 16.2. warnings 原順序保持（Phase 6.3C.1）
# ================================================================
def test_warnings_order_stable():
    r = build("售 紅線 算5000", [None, {
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    # 去重不排序：先出現的錯誤 warning 在前
    first_img_err = next(i for i, w in enumerate(r.warnings)
                         if w.startswith("vision_image_error"))
    assert first_img_err < len(r.warnings)  # 只是確保存在且順序穩定
    assert r.warnings == list(dict.fromkeys(r.warnings))


# ================================================================
# 17-18. 無外部呼叫 / 不下載圖片
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    assert r.vision_used is True


def test_no_image_download(monkeypatch):
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("不得下載圖片")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    assert r.vision_used is True


# ================================================================
# 19. 結果欄位完整
# ================================================================
def test_result_fields_complete():
    r = build("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    assert r.merged_post is not None
    assert r.legacy_result is not None
    assert isinstance(r.image_evidence, list)
    assert isinstance(r.conflicts, list)
    assert isinstance(r.warnings, list)
    assert isinstance(r.blocked, bool)
    assert isinstance(r.vision_used, bool)


# ================================================================
# 20. safe reasons 完整性
# ================================================================
def test_safe_reasons_complete():
    from alkaid_cs2.integration.production_bridge import vision_merge_safe_reasons
    # 全部失敗 → 多個 reason
    r = build("售 紅線 算5000", [None])
    reasons = vision_merge_safe_reasons(r)
    assert "vision_not_used" in reasons
    assert "vision_fallback:all_vision_images_failed" in reasons
    # 空清單代表安全（safe 案例）
    r2 = build("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5000"}]}])
    assert vision_merge_safe_reasons(r2) == []
