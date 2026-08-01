"""
vision_production.py — Vision 受控 production 整合（V2 Phase 6.3C）

流程：
  RawPostInput → parse_post() → 逐張 vision_payload_to_evidence()
  → deduplicate_image_evidence() → merge_text_and_image_evidence()
  → to_legacy_skin_info() → VisionMergeProductionResult

限制：不呼叫 vision_analyzer、不下載圖片、不查 BUFF、不呼叫模型、不換算。
"""
import copy
import json
from dataclasses import dataclass, field
from typing import Any

from alkaid_cs2.adapters.legacy_adapter import LegacyAdapterResult, to_legacy_skin_info
from alkaid_cs2.adapters.vision_adapter import vision_payload_to_evidence
from alkaid_cs2.domain.evidence_merge import EvidenceConflict, MergedEvidenceResult
from alkaid_cs2.domain.image_evidence import ImageEvidence
from alkaid_cs2.domain.parsed_post import ParsedPost
from alkaid_cs2.domain.raw_post import RawPostInput
from alkaid_cs2.pipeline.parse_pipeline import parse_post
from alkaid_cs2.services.evidence_merger import merge_text_and_image_evidence
from alkaid_cs2.services.image_deduplicator import deduplicate_image_evidence


@dataclass
class VisionImageInput:
    image_index: int
    image_url: str
    payload: object
    image_hash: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.image_index, bool) or not isinstance(self.image_index, int):
            raise TypeError(f"image_index 必須是非負 int，收到 {type(self.image_index).__name__}")
        if self.image_index < 0:
            raise ValueError(f"image_index 不可為負數，收到 {self.image_index}")
        if not isinstance(self.image_url, str) or not self.image_url.strip():
            raise ValueError("image_url 必須是非空 str")
        if self.image_hash is not None and (not isinstance(self.image_hash, str) or not self.image_hash.strip()):
            raise ValueError("image_hash 若非 None 必須是非空 str")
        # payload 可為 dict / list / JSON string / None；拒絕 bool
        if isinstance(self.payload, bool) or (self.payload is not None and not isinstance(self.payload, (dict, list, str))):
            raise TypeError(f"payload 必須是 dict/list/str/None，收到 {type(self.payload).__name__}")
        # defensive copy：不與呼叫端共享
        self.image_url = self.image_url.strip()
        if self.image_hash is not None:
            self.image_hash = self.image_hash.strip()
        self.payload = copy.deepcopy(self.payload)


@dataclass
class VisionMergeProductionResult:
    merged_post: ParsedPost | None
    legacy_result: LegacyAdapterResult | None
    image_evidence: list[ImageEvidence] = field(default_factory=list)
    conflicts: list[EvidenceConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
    fallback_reason: str | None = None
    vision_used: bool = False

    def __post_init__(self) -> None:
        if self.merged_post is not None and not isinstance(self.merged_post, ParsedPost):
            raise TypeError(f"merged_post 必須是 ParsedPost 或 None，收到 {type(self.merged_post).__name__}")
        if self.legacy_result is not None and not isinstance(self.legacy_result, LegacyAdapterResult):
            raise TypeError(f"legacy_result 必須是 LegacyAdapterResult 或 None，收到 {type(self.legacy_result).__name__}")
        for ev in self.image_evidence:
            if not isinstance(ev, ImageEvidence):
                raise TypeError(f"image_evidence 每筆必須是 ImageEvidence，收到 {type(ev).__name__}")
        for c in self.conflicts:
            if not isinstance(c, EvidenceConflict):
                raise TypeError(f"conflicts 每筆必須是 EvidenceConflict，收到 {type(c).__name__}")
        if not isinstance(self.warnings, list):
            raise TypeError("warnings 必須是 list")
        if any(not isinstance(w, str) or not w.strip() for w in self.warnings):
            raise ValueError("warnings 不得含空白字串")
        if not isinstance(self.blocked, bool):
            raise TypeError(f"blocked 必須是 bool，收到 {type(self.blocked).__name__}")
        if self.fallback_reason is not None and not self.fallback_reason.strip():
            raise ValueError("fallback_reason 若非 None 不可空白")
        if not isinstance(self.vision_used, bool):
            raise TypeError(f"vision_used 必須是 bool，收到 {type(self.vision_used).__name__}")
        # blocked=True 時 legacy_result 不得為可放行資料
        if self.blocked and self.legacy_result is not None and not self.legacy_result.blocked:
            if self.legacy_result.legacy_data is not None:
                raise ValueError("blocked=True 時 legacy_result 不得為可放行資料")
        self.image_evidence = list(self.image_evidence)
        self.conflicts = list(self.conflicts)
        self.warnings = list(self.warnings)


# 已知可捕捉的圖片處理例外（禁止 except Exception: pass）
_KNOWN_IMAGE_ERRORS = (ValueError, TypeError, json.JSONDecodeError)


def _inline_url(post_id: str, image_index: int) -> str:
    return f"inline://post/{post_id}/image/{image_index}"


def build_vision_merged_result(
    post: RawPostInput,
    *,
    vision_inputs: list[VisionImageInput],
    full_dict: dict[str, str],
    pattern_dict: dict[str, str],
    weapon_map: dict[str, str],
) -> VisionMergeProductionResult:
    if not isinstance(post, RawPostInput):
        raise TypeError(f"post 必須是 RawPostInput，收到 {type(post).__name__}")
    if not isinstance(vision_inputs, list):
        raise TypeError(f"vision_inputs 必須是 list，收到 {type(vision_inputs).__name__}")

    warnings: list[str] = []

    # 1. deterministic text parse
    text_post = parse_post(post, full_dict=full_dict, pattern_dict=pattern_dict,
                           weapon_map=weapon_map)

    # 2. 逐張圖片轉換（一張失敗不影響其他）
    evidences: list[ImageEvidence] = []
    failed = 0
    for vi in vision_inputs:
        try:
            ev = vision_payload_to_evidence(
                vi.payload,
                image_index=vi.image_index,
                image_url=vi.image_url or _inline_url(post.post_id, vi.image_index),
                image_hash=vi.image_hash,
            )
            # adapter 層已解析錯誤（None / invalid JSON 等）→ 視為該圖失敗
            if ev.errors:
                failed += 1
                warnings.append(
                    f"vision_image_error:{vi.image_index}:{';'.join(ev.errors)}")
                continue
            evidences.append(ev)
        except _KNOWN_IMAGE_ERRORS as exc:
            failed += 1
            warnings.append(f"vision_image_error:{vi.image_index}:{type(exc).__name__}")

    if not evidences:
        # 所有圖片都失敗：保留 text-only ParsedPost
        legacy = to_legacy_skin_info(text_post)
        return VisionMergeProductionResult(
            merged_post=text_post,
            legacy_result=legacy,
            image_evidence=[],
            conflicts=[],
            warnings=list(dict.fromkeys(warnings)),
            blocked=legacy.blocked,
            fallback_reason="all_vision_images_failed",
            vision_used=False,
        )

    # 3. 去重
    dedup = deduplicate_image_evidence(evidences)
    dup_count = len(evidences) - len(dedup)
    if dup_count:
        warnings.append(f"duplicate_images_removed:{dup_count}")

    # 4. 合併
    merged: MergedEvidenceResult = merge_text_and_image_evidence(text_post, dedup)

    # 記錄 text 基數（供 metrics 計算 items/prices added）
    md = dict(merged.parsed_post.metadata)
    md["text_item_count"] = len(text_post.items)
    md["text_price_count"] = len(text_post.prices)
    merged.parsed_post.metadata = md

    # 5. legacy 轉換
    legacy = to_legacy_skin_info(merged.parsed_post)

    return VisionMergeProductionResult(
        merged_post=merged.parsed_post,
        legacy_result=legacy,
        image_evidence=list(dedup),
        conflicts=list(merged.conflicts),
        warnings=list(dict.fromkeys(warnings + list(merged.warnings))),
        blocked=legacy.blocked,
        fallback_reason=None,
        vision_used=True,
    )
