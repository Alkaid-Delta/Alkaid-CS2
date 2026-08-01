"""
test_crawler_vision_pipeline.py — crawler Vision pipeline 整合測試（Phase 6.3D）

synthetic post → analyze_post_images → build_post_vision_fields
→ extract_vision_inputs_from_post → parse_post_for_production
全部使用 stub / fixture，不碰網路。
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


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class FakeDownloader:
    def __init__(self, urls, bad=None, timeout_bad=None):
        from alkaid_cs2.integration.crawler_vision import normalize_crawler_image_url
        unique, seen = [], set()
        for u in urls:
            n = normalize_crawler_image_url(u)
            if n in seen:
                continue
            seen.add(n)
            unique.append(u)
        self.bytes_map = {u: f"img{i}".encode() for i, u in enumerate(unique)}
        self.bad = {normalize_crawler_image_url(u) for u in (bad or [])}
        self.timeout_bad = {normalize_crawler_image_url(u) for u in (timeout_bad or [])}

    def __call__(self, url, timeout):
        from alkaid_cs2.integration.crawler_vision import normalize_crawler_image_url
        key = normalize_crawler_image_url(url)
        if key in self.timeout_bad:
            raise TimeoutError("timeout")
        if key in self.bad:
            raise ConnectionError("conn error")
        return self.bytes_map[url]


def run_crawler_vision(fixture, bad=None, timeout_bad=None, max_images=5):
    """fixture → vision results → post vision 欄位。"""
    data = load_fixture(fixture)
    post = dict(data["post"])
    urls = post["images"]
    per = list(zip(data["vision_responses"]["type"], data["vision_responses"]["items"]))
    by_bytes = {f"img{i}".encode(): per[i] for i in range(len(per))}

    def fake_analyzer(image_bytes, custom_prompt=None, retry=None):
        t, its = by_bytes.get(image_bytes, (None, []))
        if "判斷這張" in custom_prompt:
            return t
        return its

    dl = FakeDownloader(urls, bad=bad, timeout_bad=timeout_bad)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=fake_analyzer,
        max_images=max_images, timeout_seconds=20)
    fields = build_post_vision_fields(results)
    post["vision_inputs"] = fields["vision_inputs"]
    post["vision_payloads"] = fields["vision_payloads"]
    if fields["legacy_items"]:
        post["items"] = fields["legacy_items"]
    if fields["currency"]:
        post["currency"] = fields["currency"]
    return post, results


# ================================================================
# 1-3. crawler 產出
# ================================================================
def test_crawler_emits_vision_inputs():
    post, results = run_crawler_vision("three_images_all_success.json")
    assert post["vision_inputs"] is not None
    assert len(post["vision_inputs"]) == 3
    for v in post["vision_inputs"]:
        assert "image_index" in v
        assert "image_url" in v
        assert "payload" in v


def test_crawler_emits_all_image_indexes():
    post, results = run_crawler_vision("three_images_all_success.json")
    assert [v["image_index"] for v in post["vision_inputs"]] == [0, 1, 2]


def test_crawler_first_success_does_not_break():
    post, results = run_crawler_vision("three_images_all_success.json")
    assert len(results) == 3, "第一張成功後仍分析後續"
    assert all(r.success for r in results)


# ================================================================
# 4-5. 失敗隔離與 legacy items
# ================================================================
def test_failed_image_does_not_stop_post():
    post, results = run_crawler_vision("first_failed_second_success.json",
                                       bad=["https://img/bad.jpg"])
    assert results[0].success is False
    assert results[1].success is True
    assert len(post["vision_inputs"]) == 1
    assert post["vision_inputs"][0]["image_index"] == 1, "成功圖保持原 index"


def test_legacy_items_preserved():
    post, results = run_crawler_vision("first_failed_second_success.json",
                                       bad=["https://img/bad.jpg"])
    assert post["items"], "legacy post[items] 存在"
    assert post["items"][0]["name"] == "AK-47 | 红线", "用第一個成功 payload"


# ================================================================
# 6-8. 消費與去重
# ================================================================
def test_payloads_consumed_by_extract_vision_inputs():
    post, _ = run_crawler_vision("three_images_all_success.json")
    vis = aa.extract_vision_inputs_from_post(post)
    assert vis is not None and len(vis) == 3
    assert vis[0].image_index == 0
    assert vis[1].image_index == 1
    assert vis[2].image_index == 2


def test_duplicate_images_only_once():
    post, results = run_crawler_vision("duplicate_urls.json")
    assert len(results) == 2, "重複 URL 只分析一次"
    assert len(post["vision_inputs"]) == 2


def test_image_order_preserved():
    post, _ = run_crawler_vision("three_images_all_success.json")
    names = [v["payload"]["items"][0]["name"] for v in post["vision_inputs"]]
    assert names == ["AK-47 | 红线", "AK-47 | 火神", "AWP | 巨龙传说"], \
        "payload 順序與原圖片順序相同"


# ================================================================
# 9-11. 圖片類型保留
# ================================================================
def test_inventory_payload_preserved():
    post, results = run_crawler_vision("mixed_image_kinds.json")
    kinds = [r.payload.get("type") for r in results]
    assert kinds[0] == "inventory", "inventory payload 保存（crawler 不刪除）"
    assert "inventory" in kinds


def test_market_payload_preserved():
    post, results = run_crawler_vision("mixed_image_kinds.json")
    market = [r for r in results if r.payload and r.payload.get("type") == "market"]
    assert market, "market 圖 payload 保存"
    assert market[0].payload["type"] == "market", \
        f"直接驗證 type: {market[0].payload['type']}"


def test_inspect_payload_preserved():
    post, results = run_crawler_vision("mixed_image_kinds.json")
    inspect = [r for r in results if r.payload and r.payload.get("type") == "inspect"]
    assert inspect, "inspect 圖 payload 保存"
    assert inspect[0].payload["type"] == "inspect", \
        f"直接驗證 type: {inspect[0].payload['type']}"
    assert "price" not in inspect[0].payload["items"][0] or \
        inspect[0].payload["items"][0].get("price") is None, \
        "crawler 不自行創造價格"


def test_payment_payload_preserved():
    post = {"id": "p_pay", "author": "A", "url": "u", "content": "已付款",
            "images": ["https://img/pay.jpg"]}
    per = [({"type": "payment"}, [])]
    by_bytes = {b"img0": per[0]}

    def fake_analyzer(image_bytes, custom_prompt=None, retry=None):
        t, its = by_bytes.get(image_bytes, (None, []))
        if "判斷這張" in custom_prompt:
            return t
        return its

    dl = FakeDownloader(post["images"])
    results = analyze_post_images(
        post["images"], download_image_func=dl, analyze_image_func=fake_analyzer,
        max_images=5, timeout_seconds=20)
    assert results[0].success is True, "payment 空 items 保存"
    assert results[0].payload["type"] == "payment"
    fields = build_post_vision_fields(results)
    assert fields["legacy_items"] == [], "payment 不建立 legacy items"


# ================================================================
# 12. 幣別
# ================================================================
def test_currencies_preserved():
    post, _ = run_crawler_vision("multi_currency.json")
    assert post["currency"] == "TWD", "currency 不被最後一張覆蓋"
    payloads = [v["payload"] for v in post["vision_inputs"]]
    assert payloads[0]["items"][0]["currency"] == "TWD"
    assert payloads[1]["items"][0]["currency"] == "RMB", "各自保留幣別"


# ================================================================
# 13. 全部失敗仍可 text pipeline
# ================================================================
def test_all_failed_text_pipeline_still_runs():
    post, results = run_crawler_vision(
        "all_images_failed.json",
        timeout_bad=["https://img/bad1.jpg", "https://img/bad2.jpg"])
    assert all(not r.success for r in results)
    assert post["vision_inputs"] is None
    # text-only production 仍可執行（safe 模式）
    r = parse_post_for_production(
        post_id=post["id"], author=post["author"], link=post["url"],
        post_text=post["content"], image_urls=post["images"],
        vision_inputs=None,
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP,
        legacy_parser=lambda t: {"market_hash_name": "Redline", "seller_price": 5000,
                                 "confidence": "high"},
        mode="safe",
    )
    assert r.source in ("v2", "legacy")


# ================================================================
# 14-15. 無真實網路 / 無真實 Vision API
# ================================================================
def test_no_real_network(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    post, results = run_crawler_vision("three_images_all_success.json")
    assert len(results) == 3


def test_no_real_vision_api():
    post, results = run_crawler_vision("three_images_all_success.json")
    # 全部走 fake analyzer（無 OPENROUTER 呼叫）
    assert all(r.success for r in results)
