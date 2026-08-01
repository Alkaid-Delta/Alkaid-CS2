"""
test_image_deduplicator.py — 圖片 metadata 去重測試（Phase 6.3A）
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.image_evidence import (  # noqa: E402
    ImageEvidence,
    ImageEvidenceSource,
    ImageKind,
    ImagePlatform,
)
from alkaid_cs2.services.image_deduplicator import (  # noqa: E402
    deduplicate_image_evidence,
    normalize_image_url,
)


def make_ev(idx=0, url="https://scontent.fbcdn.net/v/t1/1.jpg?oh=AAA&oe=BBB&id=1",
            h=None):
    return ImageEvidence(
        image_index=idx,
        image_url=url,
        image_hash=h,
        image_kind=ImageKind.SINGLE_ITEM,
        platform=ImagePlatform.FACEBOOK,
        source=ImageEvidenceSource.VISION,
        raw_result={},
        confidence=0.8,
    )


# ---------------------------------------------------------------
# 1. 相同 hash 去重
# ---------------------------------------------------------------
def test_same_hash_deduplicated():
    a = make_ev(idx=0, url="https://a/1.jpg", h="same")
    b = make_ev(idx=1, url="https://b/2.jpg", h="same")
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1
    assert result[0] is a


# ---------------------------------------------------------------
# 2. 相同 normalized URL 去重
# ---------------------------------------------------------------
def test_same_normalized_url_deduplicated():
    a = make_ev(idx=0, url="https://scontent.fbcdn.net/v/t1/1.jpg?oh=AAA&oe=BBB&id=1")
    b = make_ev(idx=1, url="https://scontent.fbcdn.net/v/t1/1.jpg?oe=BBB&oh=AAA&id=1")
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1, "FB 簽章參數排序不同仍應去重"


# ---------------------------------------------------------------
# 3. 不同圖片保留
# ---------------------------------------------------------------
def test_different_images_preserved():
    a = make_ev(idx=0, url="https://a/1.jpg")
    b = make_ev(idx=1, url="https://b/2.jpg")
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 2


# ---------------------------------------------------------------
# 4. 相同商品不同圖保留（不依名稱合併）
# ---------------------------------------------------------------
def test_same_item_different_image_preserved():
    a = make_ev(idx=0, url="https://a/1.jpg")
    b = make_ev(idx=1, url="https://b/2.jpg")
    # 兩張圖的 raw_result 有相同商品名稱 → 仍保留兩張
    a.raw_result = {"name": "AK-47 | Redline"}
    b.raw_result = {"name": "AK-47 | Redline"}
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 2, "不得因相似名稱合併圖片"


# ---------------------------------------------------------------
# 5. 同 image_index 重複處理
# ---------------------------------------------------------------
def test_duplicate_image_index_handled():
    a = make_ev(idx=0, url="https://a/1.jpg")
    b = make_ev(idx=0, url="https://b/2.jpg")  # 同 index 不同 URL
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1, "同 image_index 視為重複"


# ---------------------------------------------------------------
# 6. 輸入不被修改
# ---------------------------------------------------------------
def test_input_not_mutated():
    evs = [make_ev(idx=0), make_ev(idx=1)]
    result = deduplicate_image_evidence(evs)
    assert len(evs) == 2  # 原 list 不變
    assert result is not evs


# ---------------------------------------------------------------
# 7. 非法輸入 raise
# ---------------------------------------------------------------
def test_invalid_input_type_raises():
    with pytest.raises(TypeError):
        deduplicate_image_evidence("not-list")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        deduplicate_image_evidence([make_ev(), "not-evidence"])  # type: ignore[list-item]


# ---------------------------------------------------------------
# 8. 穩定順序
# ---------------------------------------------------------------
def test_stable_order():
    a = make_ev(idx=0, url="https://a/1.jpg")
    b = make_ev(idx=1, url="https://b/2.jpg")
    c = make_ev(idx=2, url="https://c/3.jpg")
    result = deduplicate_image_evidence([a, b, c])
    assert result == [a, b, c], "維持原順序"


# ---------------------------------------------------------------
# 9. normalize_image_url 基本行為
# ---------------------------------------------------------------
def test_normalize_image_url_basics():
    assert normalize_image_url("https://x/1.jpg#frag") == "https://x/1.jpg"
    assert normalize_image_url("https://x/1.jpg?a=1&b=2") == "https://x/1.jpg?a=1&b=2"
    assert normalize_image_url("https://x/1.jpg?b=2&a=1") == "https://x/1.jpg?a=1&b=2"


# ================================================================
# Phase 6.3A.1 — 交叉去重
# ================================================================
# 10. 不同 hash、相同 image_index → 去重
# ---------------------------------------------------------------
def test_different_hash_same_index_deduplicated():
    a = make_ev(idx=0, url="https://a/1.jpg", h="h1")
    b = make_ev(idx=0, url="https://b/2.jpg", h="h2")
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1, "不同 hash 但同 image_index 仍應去重"


# ---------------------------------------------------------------
# 11. 不同 hash、相同 normalized URL → 去重
# ---------------------------------------------------------------
def test_different_hash_same_url_deduplicated():
    a = make_ev(idx=0, url="https://scontent.fbcdn.net/v/t1/1.jpg?oh=AAA&id=1", h="h1")
    b = make_ev(idx=1, url="https://scontent.fbcdn.net/v/t1/1.jpg?id=1&oh=AAA", h="h2")
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1, "不同 hash 但 URL 相同仍應去重"


# ---------------------------------------------------------------
# 12. 第一筆無 hash、第二筆有 hash 但 URL 相同 → 去重
# ---------------------------------------------------------------
def test_no_hash_then_hash_same_url_deduplicated():
    a = make_ev(idx=0, url="https://x/1.jpg", h=None)
    b = make_ev(idx=1, url="https://x/1.jpg", h="h2")
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1
    assert result[0] is a


# ---------------------------------------------------------------
# 13. 第一筆有 hash、第二筆無 hash 但 URL 相同 → 去重
# ---------------------------------------------------------------
def test_hash_then_no_hash_same_url_deduplicated():
    a = make_ev(idx=0, url="https://x/1.jpg", h="h1")
    b = make_ev(idx=1, url="https://x/1.jpg", h=None)
    result = deduplicate_image_evidence([a, b])
    assert len(result) == 1
    assert result[0] is a
