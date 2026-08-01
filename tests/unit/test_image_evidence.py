"""
test_image_evidence.py — ImageEvidence 領域模型測試（Phase 6.3A）
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


def make_evidence(**overrides) -> ImageEvidence:
    base = dict(
        image_index=0,
        image_url="https://scontent.fbcdn.net/v/t1.0/abc.jpg",
        image_hash="hash123",
        image_kind=ImageKind.SINGLE_ITEM,
        platform=ImagePlatform.FACEBOOK,
        source=ImageEvidenceSource.VISION,
        raw_result={"type": "single"},
        item_candidates=[],
        price_candidates=[],
        confidence=0.8,
        warnings=[],
        errors=[],
    )
    base.update(overrides)
    return ImageEvidence(**base)


# ---------------------------------------------------------------
# 1. 正常建立
# ---------------------------------------------------------------
def test_valid_image_evidence():
    e = make_evidence()
    assert e.image_index == 0
    assert e.image_url.startswith("https://")
    assert e.image_hash == "hash123"
    assert e.image_kind is ImageKind.SINGLE_ITEM
    assert e.platform is ImagePlatform.FACEBOOK
    assert e.source is ImageEvidenceSource.VISION
    assert e.confidence == 0.8


# ---------------------------------------------------------------
# 2-3. image_index 驗證
# ---------------------------------------------------------------
def test_negative_image_index_raises():
    with pytest.raises(ValueError):
        make_evidence(image_index=-1)


def test_bool_image_index_raises():
    with pytest.raises(TypeError):
        make_evidence(image_index=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 4. image_url 空白
# ---------------------------------------------------------------
def test_blank_image_url_raises():
    with pytest.raises(ValueError):
        make_evidence(image_url="")


# ---------------------------------------------------------------
# 5-7. enum 型別錯誤
# ---------------------------------------------------------------
def test_wrong_kind_type_raises():
    with pytest.raises(TypeError):
        make_evidence(image_kind="single_item")  # type: ignore[arg-type]


def test_wrong_platform_type_raises():
    with pytest.raises(TypeError):
        make_evidence(platform="facebook")  # type: ignore[arg-type]


def test_wrong_source_type_raises():
    with pytest.raises(TypeError):
        make_evidence(source="vision")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 8-9. item / price 型別錯誤
# ---------------------------------------------------------------
def test_wrong_item_type_raises():
    with pytest.raises(TypeError):
        make_evidence(item_candidates=["not-item"])  # type: ignore[arg-type]


def test_wrong_price_type_raises():
    with pytest.raises(TypeError):
        make_evidence(price_candidates=["not-price"])  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 10-11. confidence 驗證
# ---------------------------------------------------------------
def test_confidence_out_of_range():
    with pytest.raises(ValueError):
        make_evidence(confidence=-0.1)
    with pytest.raises(ValueError):
        make_evidence(confidence=1.5)


def test_confidence_nan_inf():
    with pytest.raises(ValueError):
        make_evidence(confidence=float("nan"))
    with pytest.raises(ValueError):
        make_evidence(confidence=float("inf"))


# ---------------------------------------------------------------
# 12. warnings/errors 空白
# ---------------------------------------------------------------
def test_blank_warning_error_raises():
    with pytest.raises(ValueError):
        make_evidence(warnings=["ok", ""])
    with pytest.raises(ValueError):
        make_evidence(errors=["  "])


# ---------------------------------------------------------------
# 13. mutable 欄位獨立
# ---------------------------------------------------------------
def test_mutable_fields_independent():
    w = ["w1"]
    e1 = make_evidence(warnings=w)
    e2 = make_evidence(warnings=["w2"])
    w.append("mutated")
    assert "mutated" not in e1.warnings
    assert e1.warnings is not e2.warnings
    assert e1.item_candidates is not e2.item_candidates


# ---------------------------------------------------------------
# 14. raw_result defensive copy
# ---------------------------------------------------------------
def test_raw_result_defensive_copy():
    raw = {"type": "single", "items": []}
    e = make_evidence(raw_result=raw)
    raw["type"] = "multi"  # 外部修改
    assert e.raw_result["type"] == "single"
