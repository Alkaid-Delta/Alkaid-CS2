"""
test_crawler_multi_image.py — crawler 多圖片處理測試（Phase 6.3D）
"""
import sys
import os
import json

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.integration.crawler_vision import (  # noqa: E402
    CrawlerVisionImageResult,
    analyze_post_images,
    analyze_single_post_image,
    build_post_vision_fields,
    deduplicate_post_image_refs,
    deduplicate_post_image_urls,
    get_max_vision_images_per_post,
    get_vision_image_timeout_seconds,
    normalize_crawler_image_url,
)

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "crawler_vision")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


class FakeDownloader:
    """依 URL 回 bytes；可標記失敗 URL（bad/timeout 以 normalize key 比對）。"""

    def __init__(self, urls, bad=None, timeout_bad=None):
        # 保留第一次出現的 original_url 作下載鍵
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
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        key = normalize_crawler_image_url(url)
        if key in self.timeout_bad:
            raise TimeoutError("timeout")
        if key in self.bad:
            raise ConnectionError("conn error")
        return self.bytes_map[url]


def make_fake_analyzer(per_image):
    """per_image: list of (type_result, items_result)，依 image bytes 對應。"""
    by_bytes = {}
    for i, (t, its) in enumerate(per_image):
        by_bytes[f"img{i}".encode()] = (t, its)

    def fake(image_bytes, custom_prompt=None, retry=None):
        t, its = by_bytes.get(image_bytes, (None, []))
        if "判斷這張" in custom_prompt:
            return t
        return its

    return fake


def run_images(urls, per_image, max_images=5, downloader=None):
    dl = downloader or FakeDownloader(urls)
    ana = make_fake_analyzer(per_image)
    return analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=max_images, timeout_seconds=20,
    ), dl, ana


# ================================================================
# 1-4. URL 正規化與去重
# ================================================================
def test_normalize_image_url():
    assert normalize_crawler_image_url("  https://x/1.jpg#frag  ") == "https://x/1.jpg"
    assert normalize_crawler_image_url("https://x/1.jpg?a=1&b=2") == "https://x/1.jpg?a=1&b=2"
    assert normalize_crawler_image_url("https://x/1.jpg?b=2&a=1") == "https://x/1.jpg?a=1&b=2"
    # 保守策略：FB CDN 簽章參數（oh/oe 等）保留，只排序（不得移除可能影響資源的參數）
    assert normalize_crawler_image_url(
        "https://x/1.jpg?oh=AAA&oe=BBB&id=1") == "https://x/1.jpg?id=1&oe=BBB&oh=AAA"


def test_empty_url_removed():
    assert deduplicate_post_image_urls(["https://a/1.jpg", "", "  "]) == ["https://a/1.jpg"]


def test_duplicate_url_removed():
    urls = ["https://x/1.jpg?oh=A&id=1", "https://x/1.jpg?id=1&oh=A", "https://x/2.jpg"]
    assert deduplicate_post_image_urls(urls) == ["https://x/1.jpg?oh=A&id=1", "https://x/2.jpg"], \
        "保留第一個 original_url（不下載 normalized key）"


def test_stable_url_order():
    urls = ["https://x/2.jpg", "https://x/1.jpg", "https://x/1.jpg"]
    assert deduplicate_post_image_urls(urls) == ["https://x/2.jpg", "https://x/1.jpg"]


# ================================================================
# 5-7. 限制與環境設定
# ================================================================
def test_max_image_limit():
    urls = [f"https://img/{i}.jpg" for i in range(8)]
    per = [({"type": "single"}, [{"name": f"S{i}", "price": 100}]) for i in range(8)]
    results, _, _ = run_images(urls, per, max_images=5)
    assert len(results) == 5, "只分析前 5 張"
    assert [r.image_index for r in results] == [0, 1, 2, 3, 4]


def test_invalid_max_env_defaults(monkeypatch):
    monkeypatch.setenv("ALKAID_MAX_VISION_IMAGES_PER_POST", "abc")
    assert get_max_vision_images_per_post() == 5
    monkeypatch.setenv("ALKAID_MAX_VISION_IMAGES_PER_POST", "99")
    assert get_max_vision_images_per_post() == 5
    monkeypatch.setenv("ALKAID_MAX_VISION_IMAGES_PER_POST", "3")
    assert get_max_vision_images_per_post() == 3


def test_timeout_env_validation(monkeypatch):
    monkeypatch.setenv("ALKAID_VISION_IMAGE_TIMEOUT_SECONDS", "2")
    assert get_vision_image_timeout_seconds() == 20
    monkeypatch.setenv("ALKAID_VISION_IMAGE_TIMEOUT_SECONDS", "99")
    assert get_vision_image_timeout_seconds() == 20
    monkeypatch.setenv("ALKAID_VISION_IMAGE_TIMEOUT_SECONDS", "30")
    assert get_vision_image_timeout_seconds() == 30


# ================================================================
# 8-9. 單圖成功 / 三圖全處理
# ================================================================
def test_single_image_success():
    urls = ["https://img/0.jpg"]
    per = [({"type": "single"}, [{"name": "AK-47 | 红线", "price": 5000}])]
    results, _, _ = run_images(urls, per)
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].payload["type"] == "single"
    assert results[0].payload["items"][0]["name"] == "AK-47 | 红线"


def test_three_images_all_processed():
    urls = ["https://img/0.jpg", "https://img/1.jpg", "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": f"S{i}"}]) for i in range(3)]
    results, _, _ = run_images(urls, per)
    assert len(results) == 3, "三張全部處理"
    assert [r.image_index for r in results] == [0, 1, 2], "image_index 依序"


# ================================================================
# 10. 第一張成功不 break（最重要 regression）
# ================================================================
def test_first_success_does_not_break():
    urls = ["https://img/0.jpg", "https://img/1.jpg", "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}]),
           ({"type": "single"}, [{"name": "S2"}])]
    results, _, _ = run_images(urls, per)
    assert len(results) == 3, "第一張成功後仍分析後續圖片"
    assert all(r.success for r in results)


# ================================================================
# 11. 第一張失敗第二張成功
# ================================================================
def test_first_failure_second_success():
    urls = ["https://img/bad.jpg", "https://img/good.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]
    dl = FakeDownloader(urls, bad=["https://img/bad.jpg"])
    results, _, _ = run_images(urls, per, downloader=dl)
    assert results[0].success is False
    assert results[0].error_code == "download_connection_error"
    assert results[1].success is True, "第二張仍處理"


# ================================================================
# 12. 中間失敗保留原 index
# ================================================================
def test_middle_failure_preserves_indexes():
    urls = ["https://img/0.jpg", "https://img/bad.jpg", "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}]),
           ({"type": "single"}, [{"name": "S2"}])]
    dl = FakeDownloader(urls, timeout_bad=["https://img/bad.jpg"])
    results, _, _ = run_images(urls, per, downloader=dl)
    assert results[0].success and results[1].success is False and results[2].success
    assert results[2].image_index == 2, "成功 payload 保持原 index（不可重編號）"


# ================================================================
# 13. 全部失敗
# ================================================================
def test_all_images_failed():
    urls = ["https://img/bad1.jpg", "https://img/bad2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]
    dl = FakeDownloader(urls, timeout_bad=urls)
    results, _, _ = run_images(urls, per, downloader=dl)
    assert all(not r.success for r in results)
    fields = build_post_vision_fields(results)
    assert fields["vision_inputs"] is None
    assert fields["legacy_items"] == []


# ================================================================
# 14-16. 失敗繼續
# ================================================================
def test_download_timeout_continues():
    urls = ["https://img/bad.jpg", "https://img/ok.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]
    dl = FakeDownloader(urls, timeout_bad=["https://img/bad.jpg"])
    results, _, _ = run_images(urls, per, downloader=dl)
    assert results[0].error_code == "download_timeout"
    assert results[1].success is True, "timeout 後下一張繼續"


def test_vision_timeout_continues():
    urls = ["https://img/0.jpg", "https://img/1.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]

    def timeout_analyzer(image_bytes, custom_prompt=None, retry=None):
        raise TimeoutError("vision timeout")

    dl = FakeDownloader(urls)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=timeout_analyzer,
        max_images=5, timeout_seconds=20)
    assert results[0].error_code == "vision_timeout"
    assert results[1].error_code == "vision_timeout"


def test_malformed_payload_continues():
    urls = ["https://img/0.jpg", "https://img/1.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]

    def malformed_analyzer(image_bytes, custom_prompt=None, retry=None):
        if image_bytes == b"img0":
            return "not-json-at-all"  # 非 dict/list
        return [{"name": "S1"}]

    dl = FakeDownloader(urls)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=malformed_analyzer,
        max_images=5, timeout_seconds=20)
    # 分類回傳字串 → 視為 single；提取回傳字串 → items=[]（success=True 空 payload）
    assert results[1].success is True


# ================================================================
# 17. 結果驗證
# ================================================================
def test_image_result_validation():
    with pytest.raises(TypeError):
        CrawlerVisionImageResult(image_index=True, image_url="u", payload={}, success=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CrawlerVisionImageResult(image_index=0, image_url="u", payload=None, success=True)
    with pytest.raises(ValueError):
        CrawlerVisionImageResult(image_index=0, image_url="u", payload=None,
                                 success=False, error_code="")
    with pytest.raises(ValueError):
        CrawlerVisionImageResult(image_index=0, image_url="u", payload=None,
                                 success=False, error_code="x", duration_ms=-1)
    with pytest.raises(ValueError):
        CrawlerVisionImageResult(image_index=0, image_url="u", payload=None,
                                 success=False, error_code="x", retry_count=-1)


# ================================================================
# 18-19. legacy items 相容
# ================================================================
def test_legacy_items_first_success():
    urls = ["https://img/bad.jpg", "https://img/good.jpg"]
    per = [({"type": "single"}, [{"name": "SKIP"}]),
           ({"type": "single"}, [{"name": "AK-47 | 红线", "wear": "久经沙场",
                                  "price": 5000, "currency": "TWD"}])]
    dl = FakeDownloader(urls, bad=["https://img/bad.jpg"])
    results, _, _ = run_images(urls, per, downloader=dl)
    fields = build_post_vision_fields(results)
    assert fields["legacy_items"][0]["name"] == "AK-47 | 红线", \
        "legacy items 用第一個成功 payload"


def test_no_flatten_all_items():
    urls = ["https://img/0.jpg", "https://img/1.jpg"]
    per = [({"type": "single"}, [{"name": "A"}]),
           ({"type": "single"}, [{"name": "B"}])]
    results, _, _ = run_images(urls, per)
    fields = build_post_vision_fields(results)
    assert fields["legacy_items"] == [{"name": "A", "wear": "", "price": 0,
                                       "currency": "RMB"}], \
        "legacy items 只取第一個成功（不 flatten）"
    assert len(fields["vision_inputs"]) == 2, "vision_inputs 保留全部"


# ================================================================
# 20. 多幣別不覆蓋
# ================================================================
def test_multi_currency_not_overwritten():
    urls = ["https://img/twd.jpg", "https://img/rmb.jpg"]
    per = [({"type": "single"}, [{"name": "A", "price": 5000, "currency": "TWD"}]),
           ({"type": "single"}, [{"name": "A", "price": 4300, "currency": "RMB"}])]
    results, _, _ = run_images(urls, per)
    fields = build_post_vision_fields(results)
    assert fields["currency"] == "TWD", "currency 用第一個成功（不被圖二覆蓋）"
    payloads = [r.payload for r in results if r.success]
    assert payloads[0]["items"][0]["currency"] == "TWD"
    assert payloads[1]["items"][0]["currency"] == "RMB", "各自保留幣別"


# ================================================================
# 21. 輸入不被修改
# ================================================================
def test_original_images_not_mutated():
    urls = ["https://img/0.jpg", "https://img/1.jpg", "https://img/1.jpg"]
    orig = list(urls)
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]
    results, _, _ = run_images(urls, per)
    assert urls == orig, "原始 images 不被修改"


# ================================================================
# 22. 無外部呼叫
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    urls = ["https://img/0.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}])]
    results, _, _ = run_images(urls, per)
    assert results[0].success is True


# ================================================================
# 23. payload 順序穩定
# ================================================================
def test_payload_order_stable():
    urls = ["https://img/0.jpg", "https://img/bad.jpg", "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}]),
           ({"type": "single"}, [{"name": "S2"}])]
    dl = FakeDownloader(urls, bad=["https://img/bad.jpg"])
    results, _, _ = run_images(urls, per, downloader=dl)
    fields = build_post_vision_fields(results)
    assert [v["image_index"] for v in fields["vision_inputs"]] == [0, 2], \
        "成功 payload 順序與原處理順序相同"


# ================================================================
# 24. 截斷 warning
# ================================================================
def test_max_images_warning(capsys):
    urls = [f"https://img/{i}.jpg" for i in range(8)]
    per = [({"type": "single"}, [{"name": f"S{i}"}]) for i in range(8)]
    results, _, _ = run_images(urls, per, max_images=5)
    out = capsys.readouterr().out
    assert "vision_images_truncated:8:5" in out


# ================================================================
# 25. dedup metrics 數字
# ================================================================
def test_duplicate_metrics():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    urls = ["https://img/1.jpg?oh=A&id=1", "https://img/1.jpg?id=1&oh=A",
            "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]
    m = CrawlerVisionMetrics()
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    analyze_post_images(urls, download_image_func=dl, analyze_image_func=ana,
                        max_images=5, timeout_seconds=20, metrics=m)
    assert m.images_discovered == 3
    assert m.images_after_dedup == 2
    assert m.vision_attempts == 2


# ================================================================
# 26. 空 Vision 結果標記失敗（User 核心概念：empty_vision_result）
# ================================================================
def test_empty_vision_result_marked_failed():
    urls = ["https://img/0.jpg"]
    per = [({"type": "single"}, [])]  # 提取回傳空 list
    results, _, _ = run_images(urls, per)
    assert results[0].success is False, "空結果視為失敗"
    assert results[0].error_code == "empty_vision_result"
    assert results[0].payload is None
    fields = build_post_vision_fields(results)
    assert fields["vision_inputs"] is None
    assert fields["legacy_items"] == []


# ================================================================
# 27. list payload 的 legacy items（User 核心概念）
# ================================================================
def test_list_payload_legacy_items():
    from alkaid_cs2.integration.crawler_vision import (
        CrawlerVisionImageResult, build_post_vision_fields)
    r = CrawlerVisionImageResult(
        image_index=0, image_url="https://img/0.jpg",
        payload=[{"name": "AK-47 | 红线", "price": 5000, "currency": "TWD"}],
        success=True, duration_ms=10.0)
    fields = build_post_vision_fields([r])
    assert fields["legacy_items"][0]["name"] == "AK-47 | 红线", \
        "list payload 直接當 legacy items"
    assert fields["legacy_items"][0]["currency"] == "TWD"


# ================================================================
# Phase 6.3D.1 — Hardening
# ================================================================
# 28-29. URL 原值與 dedup key 分離
# ---------------------------------------------------------------
def test_normalized_key_not_used_for_download():
    urls = ["https://img/1.jpg?oh=AAA&id=1"]
    per = [({"type": "single"}, [{"name": "S0"}])]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    analyze_post_images(urls, download_image_func=dl, analyze_image_func=ana,
                        max_images=5, timeout_seconds=20)
    assert dl.calls[0][0] == "https://img/1.jpg?oh=AAA&id=1", \
        "下載必須用 original_url（含 FB 簽章參數）"


def test_fb_signed_url_preserved_for_download():
    signed = "https://scontent.fbcdn.net/v/t1/1.jpg?_nc_cat=1&oh=ABC123&oe=DEF456"
    urls = [signed, "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}])]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    analyze_post_images(urls, download_image_func=dl, analyze_image_func=ana,
                        max_images=5, timeout_seconds=20)
    assert dl.calls[0][0] == signed, "FB CDN 簽章參數必須保留於下載 URL"


# ---------------------------------------------------------------
# 30-32. 原始 image_index 保留
# ---------------------------------------------------------------
def test_duplicate_middle_preserves_original_index():
    # [A, A, B] → A index=0、B index=2（不得變 0、1）
    urls = ["https://img/0.jpg", "https://img/0.jpg", "https://img/2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S2"}])]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=5, timeout_seconds=20)
    assert [r.image_index for r in results] == [0, 2], \
        f"原始 index 保留: {[r.image_index for r in results]}"


def test_max_limit_preserves_original_indexes():
    urls = [f"https://img/{i}.jpg" for i in range(8)]
    per = [({"type": "single"}, [{"name": f"S{i}"}]) for i in range(8)]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=5, timeout_seconds=20)
    assert [r.image_index for r in results] == [0, 1, 2, 3, 4], \
        "截斷後仍用原始 index"


def test_duplicate_before_unique_keeps_original_index():
    # [A, A, A, B] → A index=0、B index=3
    urls = ["https://img/0.jpg", "https://img/0.jpg", "https://img/0.jpg",
            "https://img/3.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S3"}])]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=5, timeout_seconds=20)
    assert [r.image_index for r in results] == [0, 3]


# ---------------------------------------------------------------
# 33-35. 圖片類型保留
# ---------------------------------------------------------------
def test_market_type_preserved():
    urls = ["https://img/m.jpg"]
    per = [({"type": "market_listing"}, [{"name": "AK-47 | 红线",
                                          "price": 4300, "currency": "RMB"}])]
    results, _, _ = run_images(urls, per)
    assert results[0].success is True
    assert results[0].payload["type"] == "market", \
        f"market_listing 應映射 market: {results[0].payload['type']}"


def test_inspect_type_preserved():
    urls = ["https://img/i.jpg"]
    per = [({"type": "inspect_screenshot"}, [{"name": "AK-47 | 红线",
                                              "price": None}])]
    results, _, _ = run_images(urls, per)
    assert results[0].payload["type"] == "inspect"


def test_chat_type_preserved():
    urls = ["https://img/c.jpg"]
    per = [({"type": "chat"}, [{"name": "AK-47 | 红线", "price": 5000}])]
    results, _, _ = run_images(urls, per)
    assert results[0].payload["type"] == "chat", \
        "chat 不得被壓成 single"


# ---------------------------------------------------------------
# 36-40. HTTP / timeout 錯誤策略
# ---------------------------------------------------------------
def test_requests_timeout_translated():
    from alkaid_cs2.integration.crawler_vision import HttpDownloadError

    def timeout_dl(url, timeout):
        raise TimeoutError("timeout")

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=timeout_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].error_code == "download_timeout"
    assert results[0].retry_count == 1, "timeout 重試一次"


def test_requests_connection_error_translated():
    def conn_dl(url, timeout):
        raise ConnectionError("conn")

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=conn_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].error_code == "download_connection_error"


def test_http_404_not_retried():
    from alkaid_cs2.integration.crawler_vision import HttpDownloadError
    calls = []

    def notfound_dl(url, timeout):
        calls.append(url)
        raise HttpDownloadError(404)

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=notfound_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].error_code == "download_http_4xx"
    assert len(calls) == 1, "404 不重試"


def test_http_500_retried_once():
    from alkaid_cs2.integration.crawler_vision import HttpDownloadError
    calls = []

    def server_error_dl(url, timeout):
        calls.append(url)
        raise HttpDownloadError(500)

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=server_error_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].error_code == "download_http_5xx"
    assert len(calls) == 2, "5xx 重試一次（共 2 次呼叫）"
    assert results[0].retry_count == 1


def test_empty_body_failed():
    def empty_dl(url, timeout):
        raise ValueError("empty image body")

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=empty_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].error_code == "empty_image_body"
    assert results[0].retry_count == 0, "空 body 不重試"


# ---------------------------------------------------------------
# 41-42. continue_on_error
# ---------------------------------------------------------------
def test_continue_false_stops_at_first_failure():
    urls = ["https://img/bad.jpg", "https://img/ok.jpg", "https://img/ok2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}]),
           ({"type": "single"}, [{"name": "S2"}])]
    dl = FakeDownloader(urls, timeout_bad=["https://img/bad.jpg"])
    ana = make_fake_analyzer(per)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=5, timeout_seconds=20, continue_on_error=False)
    assert len(results) == 1, "第一張失敗立即停止"
    assert results[0].error_code == "download_timeout"


def test_continue_false_preserves_previous_success():
    urls = ["https://img/ok.jpg", "https://img/bad.jpg", "https://img/ok2.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}]),
           ({"type": "single"}, [{"name": "S1"}]),
           ({"type": "single"}, [{"name": "S2"}])]
    dl = FakeDownloader(urls, timeout_bad=["https://img/bad.jpg"])
    ana = make_fake_analyzer(per)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=5, timeout_seconds=20, continue_on_error=False)
    assert len(results) == 2, "已成功保留 + 失敗停止"
    assert results[0].success is True
    assert results[1].error_code == "download_timeout"


# ---------------------------------------------------------------
# 43-45. 一貼文一筆輸出契約
# ---------------------------------------------------------------
def test_original_post_id_preserved():
    from alkaid_cs2.integration.crawler_vision import build_crawler_output_post
    r = build_crawler_output_post({"id": "fb_123", "author": "A", "text": "售",
                                   "url": "https://fb/1"})
    assert r["id"] == "fb_123"


def test_original_post_content_preserved():
    from alkaid_cs2.integration.crawler_vision import build_crawler_output_post
    r = build_crawler_output_post({"id": "1", "author": "A", "text": "售 紅線 算5000",
                                   "url": "u"})
    assert r["content"] == "售 紅線 算5000", "原文不變（無 [圖片] 前綴改寫）"


def test_one_fb_post_emits_one_result():
    from alkaid_cs2.integration.crawler_vision import build_crawler_output_post
    p = {"id": "1", "author": "A", "text": "售 紅線", "url": "u",
         "images": ["https://img/0.jpg", "https://img/1.jpg"],
         "items": [{"name": "A"}, {"name": "B"}]}  # 2 件物品
    r = build_crawler_output_post(p)
    assert isinstance(r, dict), "一篇貼文一筆輸出（不是 list）"
    assert r["id"] == "1"


def test_multi_items_not_split_into_multiple_posts():
    from alkaid_cs2.integration.crawler_vision import build_crawler_output_post
    p = {"id": "1", "author": "A", "text": "售 紅線", "url": "u",
         "items": [{"name": "A"}, {"name": "B"}]}
    r = build_crawler_output_post(p)
    assert "p1_item0" not in str(r["id"]), "不得拆成 p{i}_item{j}"
    assert len(r["items"]) == 2, "items 保留於同一筆"


def test_all_vision_inputs_attached_to_single_post():
    from alkaid_cs2.integration.crawler_vision import build_crawler_output_post
    vis = [{"image_index": 0, "payload": {}}, {"image_index": 1, "payload": {}}]
    r = build_crawler_output_post({"id": "1", "author": "A", "text": "t",
                                   "url": "u", "vision_inputs": vis})
    assert len(r["vision_inputs"]) == 2, "全部 vision inputs 附在同一筆"


# ---------------------------------------------------------------
# 46-47. DOM extended text（不覆蓋原文、不跳過圖片）
# ---------------------------------------------------------------
def test_alt_text_does_not_replace_post_text():
    from alkaid_cs2.integration.crawler_vision import apply_dom_extended_text
    p = {"id": "1", "text": "售 紅線", "alt_text": "圖片: AK-47"}
    assert apply_dom_extended_text(p) is True
    assert p["text"] == "售 紅線", "原文不被 DOM extended text 覆蓋"
    assert p["dom_extended_text"] == "圖片: AK-47", "欄位名為 dom_extended_text"
    assert "image_alt_text" not in p, "不再使用 image_alt_text 欄位名"


def test_alt_text_does_not_skip_remaining_images():
    from alkaid_cs2.integration.crawler_vision import apply_dom_extended_text
    p = {"id": "1", "text": "售", "alt_text": "x", "images": ["https://img/0.jpg"]}
    apply_dom_extended_text(p)
    assert p["images"] == ["https://img/0.jpg"], "images 保留（照常處理）"


# ---------------------------------------------------------------
# 48. metrics 負數拒絕
# ---------------------------------------------------------------
def test_metric_negative_count_rejected():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    m = CrawlerVisionMetrics()
    with pytest.raises(ValueError):
        m.record_post(discovered=-1, after_dedup=1, truncated=0, successes=1,
                      multi_image=False)
    with pytest.raises(TypeError):
        m.record_post(discovered=1, after_dedup=1, truncated=0, successes=True,  # type: ignore[arg-type]
                      multi_image=False)
    with pytest.raises(ValueError):
        m.record_post(discovered=1, after_dedup=1, truncated=0, successes=5,
                      multi_image=False)  # successes > processed


# ---------------------------------------------------------------
# 49-51. download_fb_image requests 轉換
# ---------------------------------------------------------------
def test_download_fb_image_requests_timeout(monkeypatch):
    from alkaid_cs2.integration.crawler_vision import download_fb_image

    class FakeResp:
        status_code = 200
        content = b""

    def fake_get(url, timeout):
        import requests
        raise requests.Timeout("t")

    monkeypatch.setattr("requests.get", fake_get)
    with pytest.raises(TimeoutError):
        download_fb_image("https://img/0.jpg", 20)


def test_download_fb_image_requests_connection_error(monkeypatch):
    from alkaid_cs2.integration.crawler_vision import download_fb_image

    def fake_get(url, timeout):
        import requests
        raise requests.ConnectionError("c")

    monkeypatch.setattr("requests.get", fake_get)
    with pytest.raises(ConnectionError):
        download_fb_image("https://img/0.jpg", 20)


def test_download_fb_image_http_status(monkeypatch):
    from alkaid_cs2.integration.crawler_vision import (
        HttpDownloadError, download_fb_image)

    class FakeResp:
        status_code = 404
        content = b""

    monkeypatch.setattr("requests.get", lambda url, timeout: FakeResp())
    with pytest.raises(HttpDownloadError) as exc:
        download_fb_image("https://img/0.jpg", 20)
    assert exc.value.status == 404


def test_download_fb_image_empty_body(monkeypatch):
    from alkaid_cs2.integration.crawler_vision import download_fb_image

    class FakeResp:
        status_code = 200
        content = b""

    monkeypatch.setattr("requests.get", lambda url, timeout: FakeResp())
    with pytest.raises(ValueError):
        download_fb_image("https://img/0.jpg", 20)


def test_download_fb_image_success(monkeypatch):
    from alkaid_cs2.integration.crawler_vision import download_fb_image

    class FakeResp:
        status_code = 200
        content = b"\x89PNG"

    monkeypatch.setattr("requests.get", lambda url, timeout: FakeResp())
    assert download_fb_image("https://img/0.jpg", 20) == b"\x89PNG"


# ================================================================
# Phase 6.3D.2 — Classification & Legacy Consistency
# ================================================================
# 52-56. TYPE_PROMPT 完整類型
# ---------------------------------------------------------------
def _type_prompt():
    from alkaid_cs2.integration.crawler_vision import _TYPE_PROMPT
    return _TYPE_PROMPT


def test_type_prompt_contains_market():
    assert "market" in _type_prompt()
    assert "掛牌頁" in _type_prompt()


def test_type_prompt_contains_inspect():
    assert "inspect" in _type_prompt()
    assert "遊戲內檢視" in _type_prompt()


def test_type_prompt_contains_chat():
    assert "chat" in _type_prompt()
    assert "聊天截圖" in _type_prompt()


def test_type_prompt_contains_payment():
    assert "payment" in _type_prompt()
    assert "收據" in _type_prompt()


def test_type_prompt_contains_trade():
    assert "trade" in _type_prompt()
    assert "交換畫面" in _type_prompt()


# ---------------------------------------------------------------
# 57-60. legacy items / currency 同源
# ---------------------------------------------------------------
def _make_result(idx, payload, success=True, code=None):
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionImageResult
    return CrawlerVisionImageResult(
        image_index=idx, image_url=f"https://img/{idx}.jpg", payload=payload,
        success=success, error_code=code, duration_ms=10.0)


def test_inventory_then_twd_item_currency_twd():
    from alkaid_cs2.integration.crawler_vision import build_post_vision_fields
    results = [
        _make_result(0, {"type": "inventory", "items": []}),  # 空 items 跳過
        _make_result(1, {"type": "single", "items": [
            {"name": "AK-47 | 红线", "price": 5000, "currency": "TWD"}]}),
    ]
    fields = build_post_vision_fields(results)
    assert fields["currency"] == "TWD", "inventory 不阻斷後續 currency 選擇"
    assert fields["legacy_items"][0]["name"] == "AK-47 | 红线"


def test_first_item_payload_multi_currency_none():
    from alkaid_cs2.integration.crawler_vision import build_post_vision_fields
    results = [
        _make_result(0, {"type": "single", "items": [
            {"name": "A", "price": 1, "currency": "TWD"},
            {"name": "B", "price": 2, "currency": "RMB"}]}),
    ]
    fields = build_post_vision_fields(results)
    assert fields["currency"] is None, "同組多幣別 → None"
    assert len(fields["legacy_items"]) == 2


def test_legacy_items_and_currency_from_same_payload():
    from alkaid_cs2.integration.crawler_vision import build_post_vision_fields
    results = [
        _make_result(0, {"type": "single", "items": [
            {"name": "A", "price": 1, "currency": "TWD"}]}),
        _make_result(1, {"type": "single", "items": [
            {"name": "B", "price": 2, "currency": "RMB"}]}),
    ]
    fields = build_post_vision_fields(results)
    assert fields["legacy_items"][0]["name"] == "A"
    assert fields["currency"] == "TWD", "currency 來自同一 payload（不被圖二覆蓋）"


def test_inventory_only_currency_none():
    from alkaid_cs2.integration.crawler_vision import build_post_vision_fields
    results = [_make_result(0, {"type": "inventory", "items": []})]
    fields = build_post_vision_fields(results)
    assert fields["currency"] is None
    assert fields["legacy_items"] == []


# ---------------------------------------------------------------
# 61-65. 空 items 圖片類型規則
# ---------------------------------------------------------------
def test_payment_empty_items_preserved():
    urls = ["https://img/p.jpg"]
    per = [({"type": "payment"}, [])]  # 提取空
    results, _, _ = run_images(urls, per)
    assert results[0].success is True, "payment 空 items 視為成功"
    assert results[0].payload["type"] == "payment"
    assert results[0].payload["items"] == []


def test_payment_does_not_create_legacy_items():
    from alkaid_cs2.integration.crawler_vision import build_post_vision_fields
    results = [_make_result(0, {"type": "payment", "items": []})]
    fields = build_post_vision_fields(results)
    assert fields["legacy_items"] == [], "payment 不得建立 legacy items"
    assert fields["currency"] is None


def test_inventory_empty_items_preserved():
    urls = ["https://img/i.jpg"]
    per = [({"type": "inventory"}, [])]
    results, _, _ = run_images(urls, per)
    assert results[0].success is True
    assert results[0].payload["type"] == "inventory"


def test_inspect_empty_items_contract():
    urls = ["https://img/in.jpg"]
    per = [({"type": "inspect"}, [])]
    results, _, _ = run_images(urls, per)
    assert results[0].success is True, "inspect 空 items 保守保存（Adapter 判定）"
    assert results[0].payload["type"] == "inspect"
    assert results[0].payload["items"] == []


def test_trade_empty_items_contract():
    urls = ["https://img/t.jpg"]
    per = [({"type": "trade"}, [])]
    results, _, _ = run_images(urls, per)
    assert results[0].success is True, "trade 空 items 保守保存（Adapter 判定）"
    assert results[0].payload["type"] == "trade"


# ---------------------------------------------------------------
# 66-67. DOM 不再限制三張
# ---------------------------------------------------------------
def test_no_dom_three_image_limit():
    src = open(os.path.join(PROJECT_ROOT, "cdp_fb_crawler.py"),
               encoding="utf-8").read()
    assert "dp['images'][:3]" not in src, "DOM 階段不得限制 3 張"
    assert 'dp["images"][:3]' not in src


def test_five_images_passed_to_analyze():
    urls = [f"https://img/{i}.jpg" for i in range(5)]
    per = [({"type": "single"}, [{"name": f"S{i}"}]) for i in range(5)]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    results = analyze_post_images(
        urls, download_image_func=dl, analyze_image_func=ana,
        max_images=5, timeout_seconds=20)
    assert len(results) == 5, "5 張完整傳入；max_images=5 才在 Vision 層截斷"


# ================================================================
# Phase 6.3D.3 — URL Dedup & Retry Metrics Final Patch
# ================================================================
# 68-74. query pair 序列化
# ---------------------------------------------------------------
def test_repeated_query_values_preserved():
    k = normalize_crawler_image_url("https://x/img?a=1&a=2")
    assert "a=1&a=2" in k, f"重複 pair 不得逗號合併: {k}"
    assert ",2" not in k.split("?")[1].replace("%2C", ""), "不得合併成逗號"


def test_comma_value_not_equal_repeated_values():
    k1 = normalize_crawler_image_url("https://x/img?a=1&a=2")
    k2 = normalize_crawler_image_url("https://x/img?a=1%2C2")
    assert k1 != k2, "a=1&a=2 與 a=1,2 的 dedup key 不得相同"


def test_percent_encoded_value_stable():
    k1 = normalize_crawler_image_url("https://x/img?a=1%2C2")
    k2 = normalize_crawler_image_url("https://x/img?a=1,2")
    assert k1 == k2, "percent-encoded 值正規化後一致"


def test_blank_query_value_preserved():
    k = normalize_crawler_image_url("https://x/img?a=&b=1")
    assert "a=" in k, f"空白 query 值保留: {k}"
    assert "b=1" in k


def test_query_order_normalized():
    assert normalize_crawler_image_url("https://x/img?b=2&a=1") == \
        normalize_crawler_image_url("https://x/img?a=1&b=2")


def test_original_url_still_used_for_download():
    urls = ["https://img/1.jpg?oh=AAA&id=1"]
    per = [({"type": "single"}, [{"name": "S0"}])]
    dl = FakeDownloader(urls)
    ana = make_fake_analyzer(per)
    analyze_post_images(urls, download_image_func=dl, analyze_image_func=ana,
                        max_images=5, timeout_seconds=20)
    assert dl.calls[0][0] == "https://img/1.jpg?oh=AAA&id=1"


def test_distinct_signed_urls_not_deduplicated():
    # 不同簽章值（oh 不同）→ 不得去重
    a = "https://scontent.fbcdn.net/v/t1/1.jpg?oh=AAA&oe=1"
    b = "https://scontent.fbcdn.net/v/t1/1.jpg?oh=BBB&oe=1"
    assert normalize_crawler_image_url(a) != normalize_crawler_image_url(b)
    refs = deduplicate_post_image_refs([a, b])
    assert len(refs) == 2, "不同簽章 URL 不得被去重"


# ---------------------------------------------------------------
# 75-81. retry_count 合併
# ---------------------------------------------------------------
def _retry_downloader(fail_once):
    """第一次拋例外、第二次成功。"""
    calls = [0]

    def dl(url, timeout):
        calls[0] += 1
        if calls[0] == 1:
            return fail_once()
        return b"img0"

    return dl


def test_timeout_then_success_retry_count_one():
    dl = _retry_downloader(lambda: (_ for _ in ()).throw(TimeoutError("t")))
    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=dl,
        analyze_image_func=lambda *a, **k: [{"name": "S0"}],
        max_images=5, timeout_seconds=20)
    assert results[0].success is True
    assert results[0].retry_count == 1, f"timeout 重試後成功 retry=1: {results[0].retry_count}"


def test_connection_then_success_retry_count_one():
    dl = _retry_downloader(lambda: (_ for _ in ()).throw(ConnectionError("c")))
    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=dl,
        analyze_image_func=lambda *a, **k: [{"name": "S0"}],
        max_images=5, timeout_seconds=20)
    assert results[0].success is True
    assert results[0].retry_count == 1


def test_http_500_then_success_retry_count_one():
    from alkaid_cs2.integration.crawler_vision import HttpDownloadError
    dl = _retry_downloader(lambda: (_ for _ in ()).throw(HttpDownloadError(500)))
    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=dl,
        analyze_image_func=lambda *a, **k: [{"name": "S0"}],
        max_images=5, timeout_seconds=20)
    assert results[0].success is True
    assert results[0].retry_count == 1, f"5xx 重試後成功 retry=1: {results[0].retry_count}"


def test_retry_success_metrics_increment():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    dl = _retry_downloader(lambda: (_ for _ in ()).throw(TimeoutError("t")))
    m = CrawlerVisionMetrics()
    analyze_post_images(
        ["https://img/0.jpg"], download_image_func=dl,
        analyze_image_func=lambda *a, **k: [{"name": "S0"}],
        max_images=5, timeout_seconds=20, metrics=m)
    assert m.vision_successes == 1
    assert m.vision_retries == 1, "重試後成功的下載重試計入 metrics"
    assert m.images_downloaded == 1, "最終成功下載 1 次（嘗試 2 次）"


def test_no_retry_success_remains_zero():
    urls = ["https://img/0.jpg"]
    per = [({"type": "single"}, [{"name": "S0"}])]
    results, _, _ = run_images(urls, per)
    assert results[0].retry_count == 0, "無重試成功 retry=0"


def test_http_404_retry_zero():
    from alkaid_cs2.integration.crawler_vision import HttpDownloadError
    calls = []

    def notfound_dl(url, timeout):
        calls.append(url)
        raise HttpDownloadError(404)

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=notfound_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].retry_count == 0
    assert len(calls) == 1


def test_empty_body_retry_zero():
    def empty_dl(url, timeout):
        raise ValueError("empty")

    results = analyze_post_images(
        ["https://img/0.jpg"], download_image_func=empty_dl,
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20)
    assert results[0].retry_count == 0


# ---------------------------------------------------------------
# 82-88. 下載最終失敗的 retry metrics（record_download_retry）
# ---------------------------------------------------------------
def _always_fail_downloader(exc):
    def dl(url, timeout):
        raise exc
    return dl


def test_timeout_twice_metrics_retry_one():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    m = CrawlerVisionMetrics()
    results = analyze_post_images(
        ["https://img/0.jpg"],
        download_image_func=_always_fail_downloader(TimeoutError("t")),
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20,
        metrics=m)
    assert results[0].success is False
    assert results[0].retry_count == 1
    assert m.vision_retries == 1, "timeout 兩次失敗 → retry 計入 vision_retries"


def test_connection_twice_metrics_retry_one():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    m = CrawlerVisionMetrics()
    results = analyze_post_images(
        ["https://img/0.jpg"],
        download_image_func=_always_fail_downloader(ConnectionError("c")),
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20,
        metrics=m)
    assert results[0].retry_count == 1
    assert m.vision_retries == 1


def test_http_500_twice_metrics_retry_one():
    from alkaid_cs2.integration.crawler_vision import (
        CrawlerVisionMetrics, HttpDownloadError)
    m = CrawlerVisionMetrics()
    results = analyze_post_images(
        ["https://img/0.jpg"],
        download_image_func=_always_fail_downloader(HttpDownloadError(500)),
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20,
        metrics=m)
    assert results[0].retry_count == 1
    assert m.vision_retries == 1, "5xx 兩次失敗 → retry 計入 vision_retries"


def test_download_failure_does_not_increment_vision_attempts():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    m = CrawlerVisionMetrics()
    analyze_post_images(
        ["https://img/0.jpg"],
        download_image_func=_always_fail_downloader(TimeoutError("t")),
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20,
        metrics=m)
    assert m.vision_attempts == 0, "下載失敗不得增加 vision_attempts"
    assert m.vision_failures == 0, "下載失敗不得增加 vision_failures"
    assert m.image_download_failures == 1


def test_http_404_metrics_retry_zero():
    from alkaid_cs2.integration.crawler_vision import (
        CrawlerVisionMetrics, HttpDownloadError)
    m = CrawlerVisionMetrics()
    analyze_post_images(
        ["https://img/0.jpg"],
        download_image_func=_always_fail_downloader(HttpDownloadError(404)),
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20,
        metrics=m)
    assert m.vision_retries == 0, "4xx 不得增加 retry"
    assert m.image_download_failures == 1


def test_empty_body_metrics_retry_zero():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    m = CrawlerVisionMetrics()
    analyze_post_images(
        ["https://img/0.jpg"],
        download_image_func=_always_fail_downloader(ValueError("empty")),
        analyze_image_func=lambda *a, **k: [], max_images=5, timeout_seconds=20,
        metrics=m)
    assert m.vision_retries == 0, "空 body 不得增加 retry"


def test_record_download_retry_rejects_bool_negative():
    from alkaid_cs2.integration.crawler_vision import CrawlerVisionMetrics
    m = CrawlerVisionMetrics()
    with pytest.raises(TypeError):
        m.record_download_retry(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        m.record_download_retry("1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        m.record_download_retry(-1)
    m.record_download_retry(0)
    m.record_download_retry(2)
    assert m.vision_retries == 2
