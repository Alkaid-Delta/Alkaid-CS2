"""
test_vision_production_models.py — VisionImageInput / VisionMergeProductionResult 測試（Phase 6.3C）
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
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus  # noqa: E402
from alkaid_cs2.integration.vision_production import (  # noqa: E402
    VisionImageInput,
    VisionMergeProductionResult,
)


def make_input(**overrides) -> VisionImageInput:
    base = dict(image_index=0, image_url="https://img/1.jpg", payload={"type": "single"})
    base.update(overrides)
    return VisionImageInput(**base)


def make_post():
    return ParsedPost(post_id="p1", raw_text="x", parse_status=ParseStatus.OK,
                      source="test")


def make_evidence():
    return ImageEvidence(
        image_index=0, image_url="https://img/1.jpg", image_hash=None,
        image_kind=ImageKind.SINGLE_ITEM, platform=ImagePlatform.FACEBOOK,
        source=ImageEvidenceSource.VISION, raw_result={}, confidence=0.8,
    )


def make_result(**overrides) -> VisionMergeProductionResult:
    base = dict(
        merged_post=make_post(),
        legacy_result=None,
        image_evidence=[make_evidence()],
        conflicts=[],
        warnings=["ok"],
        blocked=False,
        fallback_reason=None,
        vision_used=True,
    )
    base.update(overrides)
    return VisionMergeProductionResult(**base)


# ================================================================
# VisionImageInput
# ================================================================
def test_valid_vision_image_input():
    vi = make_input()
    assert vi.image_index == 0
    assert vi.image_url.startswith("https://")
    assert vi.payload == {"type": "single"}
    assert vi.image_hash is None


def test_negative_image_index():
    with pytest.raises(ValueError):
        make_input(image_index=-1)


def test_bool_image_index():
    with pytest.raises(TypeError):
        make_input(image_index=True)  # type: ignore[arg-type]


def test_blank_image_url():
    with pytest.raises(ValueError):
        make_input(image_url="")


def test_blank_hash():
    with pytest.raises(ValueError):
        make_input(image_hash="  ")


# ================================================================
# Phase 6.3C.1 — 驗證補強 + defensive copy
# ================================================================
def test_payload_bool_raises():
    with pytest.raises(TypeError):
        make_input(payload=True)  # type: ignore[arg-type]


def test_image_url_whitespace_stripped():
    vi = make_input(image_url="  https://img/1.jpg  ")
    assert vi.image_url == "https://img/1.jpg"


def test_image_hash_whitespace_stripped():
    vi = make_input(image_hash="  hash123  ")
    assert vi.image_hash == "hash123"


def test_payload_nested_deepcopy():
    payload = {"type": "single", "items": [{"name": "A", "stickers": [{"x": 1}]}]}
    vi = VisionImageInput(image_index=0, image_url="https://img/1.jpg", payload=payload)
    payload["items"][0]["stickers"][0]["x"] = 999  # 修改原始巢狀
    assert vi.payload["items"][0]["stickers"][0]["x"] == 1, "外部修改不影響物件"


def test_payload_owned_copy():
    payload = {"type": "single", "items": [{"name": "A"}]}
    vi = VisionImageInput(image_index=0, image_url="https://img/1.jpg", payload=payload)
    vi.payload["items"][0]["name"] = "mutated"  # 修改物件內部
    assert payload["items"][0]["name"] == "A", "物件修改不影響原始 payload"


# ================================================================
# VisionMergeProductionResult
# ================================================================
def test_valid_result():
    r = make_result()
    assert isinstance(r.merged_post, ParsedPost)
    assert r.vision_used is True
    assert r.blocked is False


def test_wrong_merged_post_type():
    with pytest.raises(TypeError):
        make_result(merged_post="x")  # type: ignore[arg-type]


def test_wrong_legacy_result_type():
    with pytest.raises(TypeError):
        make_result(legacy_result="x")  # type: ignore[arg-type]


def test_wrong_image_evidence_type():
    with pytest.raises(TypeError):
        make_result(image_evidence=["x"])  # type: ignore[list-item]


def test_wrong_conflict_type():
    with pytest.raises(TypeError):
        make_result(conflicts=["x"])  # type: ignore[list-item]


def test_blank_warning():
    with pytest.raises(ValueError):
        make_result(warnings=["ok", ""])


def test_blocked_type():
    with pytest.raises(TypeError):
        make_result(blocked=1)  # type: ignore[arg-type]


def test_fallback_reason_blank():
    with pytest.raises(ValueError):
        make_result(fallback_reason="  ")


def test_vision_used_type():
    with pytest.raises(TypeError):
        make_result(vision_used=1)  # type: ignore[arg-type]


def test_mutable_fields_independent():
    r1 = make_result(warnings=["w1"])
    r2 = make_result(warnings=["w2"])
    r1.warnings.append("mutated")
    assert "mutated" not in r2.warnings
    assert r1.image_evidence is not r2.image_evidence
    assert r1.conflicts is not r2.conflicts
