"""
test_crawler_vision_metrics.py — CrawlerVisionMetrics 測試（Phase 6.3D）
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.integration.crawler_vision import (  # noqa: E402
    CrawlerVisionImageResult,
    CrawlerVisionMetrics,
)


def ok_result(idx=0, ms=100.0, retries=0):
    return CrawlerVisionImageResult(
        image_index=idx, image_url=f"https://img/{idx}.jpg",
        payload={"type": "single", "items": []}, success=True,
        duration_ms=ms, retry_count=retries)


def fail_result(idx=0, code="vision_error:ValueError", ms=50.0):
    return CrawlerVisionImageResult(
        image_index=idx, image_url=f"https://img/{idx}.jpg",
        payload=None, success=False, error_code=code, duration_ms=ms)


# ================================================================
# 1. 初始為零
# ================================================================
def test_metrics_initial_zero():
    m = CrawlerVisionMetrics()
    assert m.vision_attempts == 0
    assert m.vision_successes == 0
    assert m.vision_failures == 0
    assert m.posts_with_images == 0


# ================================================================
# 2. 單一成功
# ================================================================
def test_metrics_single_success():
    m = CrawlerVisionMetrics()
    m.record_result(ok_result(ms=200.0))
    assert m.vision_attempts == 1
    assert m.vision_successes == 1
    assert m.payloads_emitted == 1
    assert m.total_vision_duration_ms == 200.0


# ================================================================
# 3. 失敗
# ================================================================
def test_metrics_failure():
    m = CrawlerVisionMetrics()
    m.record_result(fail_result(code="vision_error:ValueError"))
    assert m.vision_attempts == 1
    assert m.vision_failures == 1
    assert m.vision_successes == 0


# ================================================================
# 4. timeout
# ================================================================
def test_metrics_timeout():
    m = CrawlerVisionMetrics()
    m.record_result(fail_result(code="vision_timeout"))
    assert m.vision_timeouts == 1
    assert m.vision_failures == 1


# ================================================================
# 5. 多圖
# ================================================================
def test_metrics_multi_image():
    m = CrawlerVisionMetrics()
    m.record_result(ok_result(0, 100))
    m.record_result(fail_result(1))
    m.record_result(ok_result(2, 300))
    assert m.vision_attempts == 3
    assert m.vision_successes == 2
    assert m.vision_failures == 1
    assert m.total_vision_duration_ms == 450.0  # 100 + 50 + 300
    assert m.vision_retries == 0


# ================================================================
# 6. dedup
# ================================================================
def test_metrics_dedup():
    m = CrawlerVisionMetrics()
    m.record_post(discovered=3, after_dedup=2, truncated=0, successes=2,
                  multi_image=True)
    assert m.images_discovered == 3
    assert m.images_after_dedup == 2
    assert m.multi_image_posts == 1


# ================================================================
# 7. truncated
# ================================================================
def test_metrics_truncated():
    m = CrawlerVisionMetrics()
    m.record_post(discovered=8, after_dedup=8, truncated=3, successes=5,
                  multi_image=True)
    assert m.images_truncated == 3


# ================================================================
# 8. 全部失敗
# ================================================================
def test_metrics_all_failed():
    m = CrawlerVisionMetrics()
    m.record_post(discovered=2, after_dedup=2, truncated=0, successes=0,
                  multi_image=False)
    assert m.posts_with_all_vision_failed == 1
    assert m.posts_with_any_vision_success == 0


# ================================================================
# 9. reset
# ================================================================
def test_metrics_reset():
    m = CrawlerVisionMetrics()
    m.record_result(ok_result())
    m.record_post(discovered=1, after_dedup=1, truncated=0, successes=1,
                  multi_image=False)
    assert m.vision_attempts == 1
    m.reset()
    assert m.vision_attempts == 0
    assert m.posts_with_images == 0
    assert m.total_vision_duration_ms == 0.0


# ================================================================
# 10. summary
# ================================================================
def test_metrics_summary():
    m = CrawlerVisionMetrics()
    m.record_result(ok_result(ms=800))
    m.record_result(ok_result(ms=1200))
    m.record_result(fail_result(code="vision_timeout"))
    m.record_post(discovered=2, after_dedup=2, truncated=0, successes=2,
                  multi_image=True)
    s = m.summary()
    assert "posts=1" in s
    assert "success=2" in s
    assert "failed=1" in s
    assert "timeouts=1" in s
    assert "avg_ms=683" in s  # (800+1200+50)/3 ≈ 683


# ================================================================
# 11. 非法值拒絕
# ================================================================
def test_bool_or_negative_values_rejected():
    m = CrawlerVisionMetrics()
    with pytest.raises(TypeError):
        m.record_result("not-a-result")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        fail_result(ms=-1)  # 負 duration
    with pytest.raises(TypeError):
        ok_result(retries=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ok_result(idx=True)  # type: ignore[arg-type]


# ================================================================
# 12. 平均 duration 零安全
# ================================================================
def test_average_duration_zero_safe():
    m = CrawlerVisionMetrics()
    s = m.summary()  # 無嘗試時不除零
    assert "avg_ms=0" in s
