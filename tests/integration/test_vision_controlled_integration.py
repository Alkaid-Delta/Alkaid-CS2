"""
test_vision_controlled_integration.py — Vision 受控 production 整合測試（Phase 6.3C）

測 parse_post_for_production 的 Vision 分流 + process_posts 接入 + extract_vision_inputs。
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import analyze_arbitrage as aa  # noqa: E402
from alkaid_cs2.integration.production_bridge import (  # noqa: E402
    _METRICS,
    ProductionParseMetrics,
    parse_post_for_production,
)
from alkaid_cs2.integration.vision_production import VisionImageInput  # noqa: E402

FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {"红线": "Redline", "紅線": "Redline", "火神": "Vulcan"}
WEAPON_MAP = {"AK-47": "AK-47"}

LEGACY_OK = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
             "seller_price": 5000, "confidence": "high",
             # Phase P2：legacy 成功路徑（字典命中）帶 verified
             "verified": True, "verified_by": "trusted_dictionary_exact"}
LEGACY_RMB = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
              "seller_price": 1000, "confidence": "high",
              # Phase P2：legacy 成功路徑（字典命中）帶 verified
              "verified": True, "verified_by": "trusted_dictionary_exact"}


def fake_legacy(text):
    if "紅線" in text or "红线" in text:
        return dict(LEGACY_OK)
    return None


def run_bridge(post_text, payloads, mode, legacy=fake_legacy, post_id="p1"):
    vision_inputs = None
    if payloads is not None:
        vision_inputs = [
            VisionImageInput(image_index=i, image_url=f"https://img/{i}.jpg", payload=p)
            for i, p in enumerate(payloads)
        ]
    return parse_post_for_production(
        post_id=post_id, author="A", link="http://u",
        post_text=post_text, image_urls=["https://img/0.jpg"],
        vision_inputs=vision_inputs,
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP,
        legacy_parser=legacy, mode=mode,
    )


SAFE_IMG = {"type": "single", "items": [{
    "market_hash_name": "AK-47 | Redline (Field-Tested)",
    "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
    "price": 5000, "currency": "TWD", "confidence": 0.9, "evidence": "售 5000"}]}
CONFLICT_IMG = {"type": "single", "items": [{
    "market_hash_name": "Redline",
    "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
    "price": 5500, "currency": "TWD", "confidence": 0.9, "evidence": "售 5500"}]}


# ================================================================
# 1. off 完全不跑 Vision
# ================================================================
def test_off_does_not_run_vision():
    r = run_bridge("售 紅線 算5000", [SAFE_IMG], "off")
    assert r.source == "legacy"
    assert r.vision_summary is None, "off 不產生 vision summary"
    assert r.data["seller_price"] == 5000


# ================================================================
# 2-3. shadow
# ================================================================
def test_shadow_returns_legacy_with_vision_diff():
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [SAFE_IMG], "shadow")
    assert r.source == "shadow_legacy"
    assert r.data == LEGACY_OK
    assert r.shadow_diff["vision_evidence_count"] == 1
    assert r.shadow_diff["vision_merge_status"] == "ok"
    assert r.vision_summary is not None


def test_shadow_malformed_vision_keeps_legacy():
    r = run_bridge("售 紅線 算5000", [None], "shadow")
    assert r.source == "shadow_legacy"
    assert r.data == LEGACY_OK, "malformed vision 不影響 legacy"
    assert any(w.startswith("vision_image_error") for w in r.warnings)


# ================================================================
# 4. safe 使用 Vision merge
# ================================================================
def test_safe_uses_vision_merge():
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [SAFE_IMG], "safe")
    assert r.source == "v2"
    assert r.data["seller_price"] == 5000
    assert "vision_merged" in r.warnings


# ================================================================
# 5. safe 衝突 → text V2（不直接 legacy）
# ================================================================
def test_safe_conflict_falls_back_to_text_v2():
    r = run_bridge("售 紅線 算5000", [CONFLICT_IMG], "safe")
    assert r.source == "v2", "text-only 安全時不得直接 fallback legacy"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)
    assert r.data["seller_price"] == 5000


# ================================================================
# 6. safe 全部圖片失敗 → text V2
# ================================================================
def test_safe_all_vision_failed_uses_text_v2():
    r = run_bridge("售 紅線 算5000", [None, "{bad-json"], "safe")
    assert r.source == "v2"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)
    assert r.vision_summary["all_failed"] is True


# ================================================================
# 7. safe text V2 也不安全 → legacy
# ================================================================
def test_safe_text_v2_unsafe_falls_back_legacy():
    # 多 selling 多 ask（text V2 AMBIGUOUS 不安全）+ Vision 衝突 → legacy
    r = run_bridge("售 紅線 算5000 火神 算14000", [CONFLICT_IMG], "safe",
                   legacy=lambda t: dict(LEGACY_RMB))
    assert r.source == "legacy"
    assert any(w.startswith("vision_fallback_to_legacy") for w in r.warnings)


# ================================================================
# 8-10. v2_only
# ================================================================
def test_v2_only_uses_vision():
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [SAFE_IMG], "v2_only")
    assert r.source == "v2"
    assert r.data["seller_price"] == 5000


def test_v2_only_conflict_uses_text_v2():
    r = run_bridge("售 紅線 算5000", [CONFLICT_IMG], "v2_only")
    assert r.source == "v2"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)


def test_v2_only_both_unsafe_skips():
    calls = []

    def spy_legacy(text):
        calls.append(text)
        return dict(LEGACY_OK)

    r = run_bridge("售 紅線 算5000 火神 算14000", [CONFLICT_IMG], "v2_only",
                   legacy=spy_legacy)
    assert r.source == "skipped"
    assert r.blocked is True
    assert r.data is None
    assert calls == [], "v2_only 不得呼叫 legacy"


# ================================================================
# 11-12. 多圖與 metrics
# ================================================================
def test_multi_image_all_processed():
    r = run_bridge("今天天氣很好", [
        {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                      "price": 7480, "confidence": 0.8}]},
        {"type": "single", "items": [{"chinese_name": "AK-47 | 火神",
                                      "price": 14000, "confidence": 0.8}]},
    ], "safe")
    assert r.vision_summary["evidence_count"] == 2, "多張圖片全部處理"


def test_duplicate_image_metrics():
    p = {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                      "price": 5000, "confidence": 0.8}]}
    m = ProductionParseMetrics()
    r1 = run_bridge("售 紅線 算5000", [p], "shadow")
    m.record(r1)
    assert m.vision_posts == 1
    # 同 URL 兩張 → duplicate
    r2 = run_bridge("售 紅線 算5000", [p], "shadow")
    m.record(r2)
    assert m.vision_posts == 2


# ================================================================
# 13-14. 掛牌價 / RMB
# ================================================================
def test_market_listing_not_used_as_seller():
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "market", "platform": "buff", "items": [{
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 4300, "currency": "RMB", "confidence": 0.8,
            "evidence": "BUFF 最低價 4300"}]}], "safe")
    assert r.source == "v2"
    assert r.data["seller_price"] == 5000, "BUFF 掛牌價不得當 seller"


def test_rmb_vision_not_converted():
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 4300, "currency": "RMB", "confidence": 0.9,
            "evidence": "售 4300 RMB"}]}], "safe")
    # RMB 圖 price 與 text 5000 TWD 衝突 → 不安全 → text V2
    assert r.source == "v2"
    assert r.data["seller_price"] == 5000, "RMB 不換算、不當 seller"


# ================================================================
# 15-17. process_posts 層
# ================================================================
@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("ALKAID_V2_PARSER_MODE", raising=False)
    monkeypatch.setattr(aa, "load_state", lambda: {})
    monkeypatch.setattr(aa, "mark_processed", lambda ids, state: None)
    monkeypatch.setattr(aa, "save_state", lambda state: None)
    monkeypatch.setattr(aa, "lookup_buff_price", lambda mh: {"price_twd": 10000, "volume": 10})
    monkeypatch.setattr(aa, "analyze_arbitrage", lambda post, buff: None)
    monkeypatch.setattr(aa, "upload_to_cloud", lambda deal: None)
    monkeypatch.setattr(aa, "save_deal_to_history", lambda deal: None)
    monkeypatch.setattr(aa, "print_deal_report", lambda deal: None)


def make_post(text, currency=None, **extra):
    p = {"id": "p1", "author": "A", "url": "http://u", "content": text,
         "images": ["https://img/0.jpg"]}
    if currency:
        p["currency"] = currency
    p.update(extra)
    return p


def test_v2_result_not_multiplied(env, monkeypatch):
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", "safe")
    post = make_post("售 AK-47 | 红线 久经沙场 算5000", currency="RMB",
                     vision_inputs=[{"image_index": 0,
                                     "image_url": "https://img/0.jpg",
                                     "payload": SAFE_IMG}])
    monkeypatch.setattr(aa, "extract_skin_info", lambda t: dict(LEGACY_RMB))
    aa.process_posts([post])
    assert post["_seller_price"] == 5000, "V2 結果不得 ×4.5"


def test_legacy_fallback_keeps_conversion(env, monkeypatch):
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", "off")
    post = make_post("售 紅線", currency="RMB")
    monkeypatch.setattr(aa, "extract_skin_info", lambda t: dict(LEGACY_RMB))
    aa.process_posts([post])
    assert post["_seller_price"] == 4500, "legacy 保留 ×4.5"


def test_seller_price_none_never_compared(env, monkeypatch):
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", "safe")
    post = make_post("今天天氣很好")  # text 無價 + 無 Vision → V2 無價
    monkeypatch.setattr(aa, "extract_skin_info", lambda t: None)
    aa.process_posts([post])  # 不得 TypeError
    assert "_seller_price" not in post or post["_seller_price"] is None


# ================================================================
# 18-20. extract_vision_inputs_from_post
# ================================================================
def test_extract_vision_inputs_from_old_items():
    post = {"id": "p1", "images": ["https://img/0.jpg"],
            "items": [{"name": "AK-47 | 红线", "price": 5000, "currency": "TWD"}]}
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is not None and len(vis) == 1
    assert vis[0].image_index == 0
    assert vis[0].payload["type"] == "single"
    assert vis[0].payload["platform"] == "facebook"


def test_extract_vision_inputs_from_payloads():
    post = {"id": "p1", "images": ["https://img/0.jpg", "https://img/1.jpg"],
            "vision_payloads": [{"type": "single"}, {"type": "single"}]}
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is not None and len(vis) == 2
    assert vis[0].image_url == "https://img/0.jpg"
    assert vis[1].image_url == "https://img/1.jpg"
    assert vis[1].image_index == 1


def test_invalid_vision_input_skipped():
    post = {"id": "p1", "images": [],
            "vision_inputs": [{"image_index": -1, "image_url": "",
                               "payload": {}}]}
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is None, "非法輸入跳過，不 crash"


# ================================================================
# Phase 6.3C.1 — Hardening
# ================================================================
FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "vision_production")


def load_fixture(name: str) -> object:
    import json as _json
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return _json.load(f)


def test_shadow_vision_input_count_correct():
    payloads = load_fixture("synthetic_post_with_payloads.json")["vision_payloads"]
    r = run_bridge("售 紅線 算5000", payloads, "shadow")
    assert r.shadow_diff["vision_input_count"] == 2, \
        f"input_count 應為 2: {r.shadow_diff['vision_input_count']}"
    assert r.shadow_diff["vision_evidence_count"] == 2


def test_extract_returns_new_instance_not_shared():
    from alkaid_cs2.integration.vision_production import VisionImageInput
    inner = {"name": "AK-47 | 红线", "price": 5000}
    orig = VisionImageInput(image_index=0, image_url="https://img/0.jpg",
                            payload={"type": "single", "items": [inner]})
    post = {"id": "p1", "images": [], "vision_inputs": [orig]}
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is not None and len(vis) == 1
    assert vis[0] is not orig, "不得回傳呼叫端同一 instance"
    vis[0].payload["items"][0]["name"] = "mutated"
    assert orig.payload["items"][0]["name"] == "AK-47 | 红线", "巢狀 payload 不得共享"


def test_post_link_precedence():
    post = {"id": "p1", "link": "https://fb.com/p/1"}
    post2 = {"id": "p1", "link": "https://fb.com/p/1", "url": "https://fb.com/url"}
    post3 = {"id": "p1"}
    assert (post.get("link") or post.get("url") or "") == "https://fb.com/p/1"
    assert (post2.get("link") or post2.get("url") or "") == "https://fb.com/p/1", \
        "link 優先於 url"
    assert (post3.get("link") or post3.get("url") or "") == ""


def test_process_posts_passes_link(env, monkeypatch):
    captured = {}
    import alkaid_cs2.integration.production_bridge as pb

    def spy_bridge(**kw):
        captured.update(kw)
        return pb.ProductionParseResult(data=dict(LEGACY_OK), source="legacy",
                                        blocked=False)

    monkeypatch.setattr(pb, "parse_post_for_production", spy_bridge)
    monkeypatch.setattr(aa, "extract_skin_info", lambda t: dict(LEGACY_OK))
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", "safe")
    post = {"id": "p1", "author": "A", "link": "https://fb.com/p/1",
            "content": "售 紅線 算5000", "images": []}
    aa.process_posts([post])
    assert captured["link"] == "https://fb.com/p/1", "post[link] 應傳入 bridge"


# ---------------------------------------------------------------
# Fixture 實際使用
# ---------------------------------------------------------------
def test_fixture_safe_vision_image_uses_merge():
    payload = load_fixture("safe_vision_image.json")
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [payload], "safe")
    assert r.source == "v2"
    assert "vision_merged" in r.warnings
    assert r.data["seller_price"] == 5000


def test_fixture_conflict_vision_falls_back_text_v2():
    payload = load_fixture("conflict_vision_image.json")
    r = run_bridge("售 紅線 算5000", [payload], "safe")
    assert r.source == "v2"
    assert any(w.startswith("vision_fallback_to_text") for w in r.warnings)


def test_fixture_buff_market_not_seller():
    payload = load_fixture("buff_market_image.json")
    r = run_bridge("售 AK-47 | 红线 久经沙场 算5000", [payload], "safe")
    assert r.source == "v2"
    assert r.data["seller_price"] == 5000, "BUFF 掛牌價不得當 seller"


def test_fixture_synthetic_items_extracted():
    post = load_fixture("synthetic_post_with_items.json")
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is not None and len(vis) == 1
    assert vis[0].payload["type"] == "single"
    assert vis[0].payload["platform"] == "facebook"
    assert vis[0].payload["items"][0]["name"] == "AK-47 | 红线"


def test_fixture_synthetic_payloads_all_processed():
    post = load_fixture("synthetic_post_with_payloads.json")
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is not None and len(vis) == 2, "多 payload 全部處理"
    assert vis[0].image_url == post["images"][0]
    assert vis[1].image_url == post["images"][1]
