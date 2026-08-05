"""
production_bridge.py — 受控 production 整合橋接（V2 Phase 6.2）

在 process_posts() 與 V2 deterministic pipeline 之間提供 feature-flag 控制的橋接：
- off：完全 legacy（預設，production 行為不變）
- shadow：legacy 為正式輸出，V2 平行執行只記錄差異
- safe：安全單商品走 V2，其餘 fallback legacy
- v2_only：只走 V2，blocked/unresolved/no price 直接跳過

安全規則：
- blocked / ambiguous / seller_price=None 不得進入舊套利流程
- 非 TWD 不得在本階段換算
- V2 結果不得再次 ×4.5
- legacy_parser raise 不得 silent pass
"""
import json
import math
import os
from dataclasses import dataclass, field
from typing import Callable

from alkaid_cs2.adapters.legacy_adapter import LegacyAdapterResult, parse_to_legacy
from alkaid_cs2.domain.evidence_merge import ConflictType
from alkaid_cs2.domain.parsed_post import ParseStatus
from alkaid_cs2.domain.market_candidate import MarketCandidate, build_market_candidates
from alkaid_cs2.domain.raw_post import RawPostInput
from alkaid_cs2.integration.vision_production import (
    VisionImageInput,
    VisionMergeProductionResult,
    build_vision_merged_result,
)

_VALID_MODES = ("off", "shadow", "safe", "v2_only")


# ============================================================
# Feature Flag
# ============================================================
def get_v2_parser_mode() -> str:
    """讀取 ALKAID_V2_PARSER_MODE（off/shadow/safe/v2_only，預設 off）。非法值 warning + fallback off。"""
    mode = os.environ.get("ALKAID_V2_PARSER_MODE", "off").strip().lower()
    if mode not in _VALID_MODES:
        print(f"[V2] ⚠️ 非法 ALKAID_V2_PARSER_MODE={mode!r}，fallback 至 off")
        return "off"
    return mode


# ============================================================
# 結果與指標
# ============================================================
@dataclass
class ProductionParseResult:
    data: dict[str, object] | None
    source: str  # legacy / v2 / shadow_legacy / skipped
    blocked: bool
    warnings: list[str] = field(default_factory=list)
    shadow_diff: dict[str, object] | None = None
    vision_summary: dict[str, object] | None = None
    structured_candidates: list[MarketCandidate] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.source not in ("legacy", "v2", "shadow_legacy", "skipped"):
            raise ValueError(f"source 必須是 legacy/v2/shadow_legacy/skipped，收到 {self.source!r}")
        if not isinstance(self.blocked, bool):
            raise TypeError("blocked 必須是 bool")
        if self.data is not None and not isinstance(self.data, dict):
            raise TypeError(f"data 必須是 dict 或 None，收到 {type(self.data).__name__}")
        if not isinstance(self.warnings, list):
            raise TypeError("warnings 必須是 list")
        if any(not isinstance(w, str) or not w.strip() for w in self.warnings):
            raise ValueError("warnings 不得含空白字串")
        if self.blocked and self.data is not None:
            raise ValueError("blocked=True 時 data 必須為 None")
        if self.shadow_diff is not None and not isinstance(self.shadow_diff, dict):
            raise TypeError("shadow_diff 必須是 dict 或 None")
        if self.vision_summary is not None and not isinstance(self.vision_summary, dict):
            raise TypeError("vision_summary 必須是 dict 或 None")
        self.warnings = list(self.warnings)  # defensive copy
        if self.shadow_diff is not None:
            self.shadow_diff = dict(self.shadow_diff)
        if self.vision_summary is not None:
            self.vision_summary = dict(self.vision_summary)
        if not isinstance(self.structured_candidates, list):
            raise TypeError("structured_candidates 必須是 list")
        for c in self.structured_candidates:
            if not isinstance(c, MarketCandidate):
                raise TypeError("structured_candidates 每筆必須是 MarketCandidate")
        self.structured_candidates = list(self.structured_candidates)


@dataclass
class ProductionParseMetrics:
    total: int = 0
    v2_used: int = 0
    legacy_used: int = 0
    shadow_runs: int = 0
    skipped: int = 0
    v2_blocked: int = 0
    v2_fallback: int = 0
    name_mismatch: int = 0
    price_mismatch: int = 0
    # Vision metrics（Phase 6.3C）
    vision_posts: int = 0
    vision_inputs: int = 0
    vision_evidence: int = 0
    vision_used: int = 0
    vision_fallback_to_text: int = 0
    vision_fallback_to_legacy: int = 0
    vision_all_failed: int = 0
    vision_conflicts: int = 0
    vision_error_conflicts: int = 0
    vision_items_added: int = 0
    vision_prices_added: int = 0
    vision_duplicate_images_removed: int = 0

    def record(self, result: ProductionParseResult) -> None:
        self.total += 1
        if result.source == "v2":
            self.v2_used += 1
        elif result.source == "shadow_legacy":
            self.shadow_runs += 1
            self.legacy_used += 1
        elif result.source == "legacy":
            self.legacy_used += 1
        elif result.source == "skipped":
            self.skipped += 1
        if result.blocked:
            self.v2_blocked += 1
        if any(w.startswith("v2_fallback") for w in result.warnings):
            self.v2_fallback += 1
        if result.shadow_diff:
            if result.shadow_diff.get("name_match") is False:
                self.name_mismatch += 1
            if result.shadow_diff.get("price_match") is False:
                self.price_mismatch += 1

        # ── Vision metrics（缺欄位不 crash；無 vision_summary 不計）──
        vs = result.vision_summary or {}
        if vs:
            self.vision_posts += 1
            self.vision_inputs += int(vs.get("input_count", 0) or 0)
            self.vision_evidence += int(vs.get("evidence_count", 0) or 0)
            self.vision_used += 1 if vs.get("used") else 0
            self.vision_fallback_to_text += 1 if vs.get("fallback_to_text") else 0
            self.vision_fallback_to_legacy += 1 if vs.get("fallback_to_legacy") else 0
            self.vision_all_failed += 1 if vs.get("all_failed") else 0
            self.vision_conflicts += int(vs.get("conflict_count", 0) or 0)
            self.vision_error_conflicts += int(vs.get("error_conflict_count", 0) or 0)
            self.vision_items_added += int(vs.get("items_added", 0) or 0)
            self.vision_prices_added += int(vs.get("prices_added", 0) or 0)
            self.vision_duplicate_images_removed += int(vs.get("duplicate_images_removed", 0) or 0)

    def summary(self) -> str:
        base = (f"V2 metrics: total={self.total} v2={self.v2_used} legacy={self.legacy_used} "
                f"shadow={self.shadow_runs} skipped={self.skipped} "
                f"v2_blocked={self.v2_blocked} v2_fallback={self.v2_fallback} "
                f"name_mismatch={self.name_mismatch} price_mismatch={self.price_mismatch}")
        vision = (f" | Vision: posts={self.vision_posts} inputs={self.vision_inputs} "
                  f"evidence={self.vision_evidence} used={self.vision_used} "
                  f"fb_text={self.vision_fallback_to_text} fb_legacy={self.vision_fallback_to_legacy} "
                  f"all_failed={self.vision_all_failed} conflicts={self.vision_conflicts} "
                  f"err_conflicts={self.vision_error_conflicts} items_added={self.vision_items_added} "
                  f"prices_added={self.vision_prices_added} dup_removed={self.vision_duplicate_images_removed}")
        return base + vision


# 全域指標實例（production 統計用）
_METRICS = ProductionParseMetrics()


# ============================================================
# seller_price 防守
# ============================================================
def is_valid_legacy_seller_price(value: object) -> bool:
    """int/Decimal（P1.3）、拒絕 bool/float、finite、> 0。

    P1.2 起 adapter 輸出 Decimal（不轉 float）；本函式同步接受
    Decimal 以維持 V2 safe 判定與 legacy 相容。
    """
    from decimal import Decimal as _D
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return False
    if not isinstance(value, (int, _D)):
        return False
    try:
        return value > 0 and not (value != value)
    except TypeError:
        return False


# ============================================================
# shadow diff
# ============================================================
def _build_shadow_diff(
    legacy_data: dict | None,
    v2_result: LegacyAdapterResult | None,
) -> dict[str, object]:
    legacy_mhn = (legacy_data or {}).get("market_hash_name")
    legacy_sp = (legacy_data or {}).get("seller_price")
    v2_mhn = None
    v2_sp = None
    v2_blocked = True
    if v2_result is not None:
        v2_blocked = v2_result.blocked
        if v2_result.legacy_data is not None:
            v2_mhn = v2_result.legacy_data.get("market_hash_name")
            v2_sp = v2_result.legacy_data.get("seller_price")

    name_match = bool(legacy_mhn and v2_mhn and legacy_mhn == v2_mhn)
    price_match = False
    if isinstance(legacy_sp, (int, float)) and isinstance(v2_sp, (int, float)):
        price_match = legacy_sp == v2_sp
    elif legacy_sp is None and v2_sp is None:
        price_match = True

    warnings = []
    if v2_result is not None:
        warnings = list(v2_result.warnings)

    return {
        "legacy_market_hash_name": legacy_mhn,
        "v2_market_hash_name": v2_mhn,
        "legacy_seller_price": legacy_sp,
        "v2_seller_price": v2_sp,
        "legacy_none": legacy_data is None,
        "v2_blocked": v2_blocked,
        "name_match": name_match,
        "price_match": price_match,
        "warnings": warnings,
    }


# ============================================================
# V2 安全條件
# ============================================================
def _v2_safe_reasons(r: LegacyAdapterResult) -> list[str]:
    """回傳不滿足安全條件的理由清單（空 = 安全）。"""
    reasons: list[str] = []
    if r.blocked:
        reasons.append("blocked")
    if r.legacy_data is None:
        reasons.append("no_legacy_data")
    else:
        mhn = r.legacy_data.get("market_hash_name")
        if not mhn:
            reasons.append("no_market_hash_name")
        sp = r.legacy_data.get("seller_price")
        if not is_valid_legacy_seller_price(sp):
            reasons.append(f"invalid_seller_price:{sp!r}")
        if r.legacy_data.get("item_role") != "selling":
            reasons.append("role_not_selling")
        if r.legacy_data.get("selection_reason") == "ambiguous":
            reasons.append("ambiguous_selection")
    if r.selected_item_index is None:
        reasons.append("no_selected_item")
    if r.selected_price_index is None:
        reasons.append("no_selected_price")
    for w in r.warnings:
        if "ambiguous" in w:
            reasons.append("ambiguous_warning")
        if "currency" in w:
            reasons.append("currency_warning")
    return reasons


# ============================================================
# Vision merge 安全條件（Phase 6.3C）
# ============================================================
def vision_merge_safe_reasons(
    result: "VisionMergeProductionResult",
) -> list[str]:
    """回傳不滿足安全條件的理由清單（空 = 安全）。"""
    reasons: list[str] = []
    if not result.vision_used:
        reasons.append("vision_not_used")
    if result.fallback_reason:
        reasons.append(f"vision_fallback:{result.fallback_reason}")
    if result.merged_post is None:
        reasons.append("no_merged_post")
    else:
        if result.merged_post.parse_status is not ParseStatus.OK:
            reasons.append("merge_status_not_ok")
        if result.merged_post.escalation_reason is not None:
            reasons.append("escalation_reason")
        for it in result.merged_post.items:
            if it.validation_error:
                reasons.append("unresolved_item")
                break
    if result.legacy_result is None:
        reasons.append("no_legacy_result")
    else:
        if result.legacy_result.blocked:
            reasons.append("legacy_blocked")
        if result.legacy_result.legacy_data is None:
            reasons.append("no_legacy_data")
        else:
            sp = result.legacy_result.legacy_data.get("seller_price")
            if not is_valid_legacy_seller_price(sp):
                reasons.append(f"invalid_seller_price:{sp!r}")
            if result.legacy_result.legacy_data.get("item_role") != "selling":
                reasons.append("not_single_safe_selling_item")
        if result.legacy_result.selected_item_index is None:
            reasons.append("no_selected_item")
        if result.legacy_result.selected_price_index is None:
            reasons.append("no_selected_price")
    for c in result.conflicts:
        if c.severity == "error":
            reasons.append("error_conflict")
        if c.conflict_type is ConflictType.PRICE_CONFLICT:
            reasons.append("price_conflict")
        if c.conflict_type is ConflictType.CURRENCY_CONFLICT:
            reasons.append("currency_conflict")
        if c.conflict_type is ConflictType.AMBIGUOUS_LINK:
            reasons.append("ambiguous_link")
    for w in result.warnings:
        if w.startswith("low_confidence"):
            reasons.append("low_confidence")
        if w.startswith("unknown_currency"):
            reasons.append("unknown_currency")
        if w.startswith("image_unknown_price"):
            reasons.append("image_unknown_price")
        if w.startswith("image_only_item"):
            reasons.append("image_only_item")
    return list(dict.fromkeys(reasons))


def _build_vision_summary(
    vp: "VisionMergeProductionResult",
    *,
    input_count: int,
    used: bool,
    fallback_to_text: bool = False,
    fallback_to_legacy: bool = False,
) -> dict[str, object]:
    """從 Vision merge 結果建立安全摘要（不含 payload / bytes / token）。"""
    error_conflicts = sum(1 for c in vp.conflicts if c.severity == "error")
    dup_removed = 0
    for w in vp.warnings:
        if w.startswith("duplicate_images_removed:"):
            dup_removed = int(w.split(":", 1)[1])
    md = vp.merged_post.metadata if vp.merged_post else {}
    text_items = int(md.get("text_item_count", len(vp.merged_post.items) if vp.merged_post else 0))
    text_prices = int(md.get("text_price_count", len(vp.merged_post.prices) if vp.merged_post else 0))
    return {
        "input_count": input_count,
        "evidence_count": len(vp.image_evidence),
        "merged_item_count": len(vp.merged_post.items) if vp.merged_post else 0,
        "merged_price_count": len(vp.merged_post.prices) if vp.merged_post else 0,
        "conflict_count": len(vp.conflicts),
        "error_conflict_count": error_conflicts,
        "status": vp.merged_post.parse_status.value if vp.merged_post else None,
        "fallback_reason": vp.fallback_reason,
        "used": used,
        "fallback_to_text": fallback_to_text,
        "fallback_to_legacy": fallback_to_legacy,
        "all_failed": vp.fallback_reason == "all_vision_images_failed",
        "items_added": len(vp.merged_post.items) - text_items if vp.merged_post else 0,
        "prices_added": len(vp.merged_post.prices) - text_prices if vp.merged_post else 0,
        "duplicate_images_removed": dup_removed,
    }


# ============================================================
# 主橋接
# ============================================================
def _run_vision_merge(
    post_id: str,
    author: str,
    link: str,
    post_text: str,
    image_urls: list[str],
    vision_inputs: list[VisionImageInput],
    full_dict: dict[str, str],
    pattern_dict: dict[str, str],
    weapon_map: dict[str, str],
    warnings: list[str],
) -> VisionMergeProductionResult:
    try:
        return build_vision_merged_result(
            RawPostInput(
                post_id=post_id, author=author, link=link,
                raw_text=post_text, image_urls=list(image_urls),
                source="facebook",
            ),
            vision_inputs=vision_inputs,
            full_dict=full_dict, pattern_dict=pattern_dict,
            weapon_map=weapon_map,
        )
    except (ValueError, TypeError) as exc:
        warnings.append(f"vision_merge_error:{type(exc).__name__}")
        return VisionMergeProductionResult(
            merged_post=None, legacy_result=None, warnings=list(warnings),
            blocked=True, fallback_reason="vision_merge_error", vision_used=False,
        )


def _extend_shadow_diff_vision(diff: dict[str, object],
                               vp: VisionMergeProductionResult,
                               *,
                               input_count: int) -> None:
    """shadow_diff 加入 Vision 欄位（不含 payload / bytes / token）。"""
    v_ld = vp.legacy_result.legacy_data if vp.legacy_result and vp.legacy_result.legacy_data else {}
    v_mhn = v_ld.get("market_hash_name")
    v_sp = v_ld.get("seller_price")
    legacy_mhn = diff.get("legacy_market_hash_name")
    legacy_sp = diff.get("legacy_seller_price")
    md = vp.merged_post.metadata if vp.merged_post else {}
    text_items = int(md.get("text_item_count", len(vp.merged_post.items) if vp.merged_post else 0))
    text_prices = int(md.get("text_price_count", len(vp.merged_post.prices) if vp.merged_post else 0))
    diff["vision_input_count"] = input_count
    diff["vision_evidence_count"] = len(vp.image_evidence)
    diff["vision_items_added"] = len(vp.merged_post.items) - text_items if vp.merged_post else 0
    diff["vision_prices_added"] = len(vp.merged_post.prices) - text_prices if vp.merged_post else 0
    diff["vision_conflict_count"] = len(vp.conflicts)
    diff["vision_error_conflict_count"] = sum(1 for c in vp.conflicts if c.severity == "error")
    diff["vision_merge_status"] = vp.merged_post.parse_status.value if vp.merged_post else None
    diff["vision_blocked"] = vp.blocked
    diff["vision_fallback_reason"] = vp.fallback_reason
    diff["text_v2_market_hash_name"] = diff.get("v2_market_hash_name")
    diff["text_v2_seller_price"] = diff.get("v2_seller_price")
    diff["vision_v2_market_hash_name"] = v_mhn
    diff["vision_v2_seller_price"] = v_sp
    diff["vision_name_match_legacy"] = bool(legacy_mhn and v_mhn and legacy_mhn == v_mhn)
    if isinstance(legacy_sp, (int, float)) and isinstance(v_sp, (int, float)):
        diff["vision_price_match_legacy"] = legacy_sp == v_sp
    elif legacy_sp is None and v_sp is None:
        diff["vision_price_match_legacy"] = True
    else:
        diff["vision_price_match_legacy"] = False


def parse_post_for_production(
    *,
    post_id: str,
    author: str,
    link: str,
    post_text: str,
    image_urls: list[str],
    vision_inputs: list[VisionImageInput] | None = None,
    full_dict: dict[str, str],
    pattern_dict: dict[str, str],
    weapon_map: dict[str, str],
    legacy_parser: Callable[[str], dict | None],
    mode: str | None = None,
) -> ProductionParseResult:
    mode = (mode or get_v2_parser_mode()).strip().lower()
    if mode not in _VALID_MODES:
        print(f"[V2] ⚠️ 非法 mode={mode!r}，視為 off")
        mode = "off"

    # ── A. off：完全 legacy（不跑 V2、不處理 Vision）──
    if mode == "off":
        data = legacy_parser(post_text)
        return ProductionParseResult(
            data=data, source="legacy", blocked=False, warnings=[], shadow_diff=None,
        )

    # ── 執行 text-only V2（shadow/safe/v2_only 都需要）──
    v2_result: LegacyAdapterResult | None = None
    v2_error: str | None = None
    try:
        v2_result = parse_to_legacy(
            RawPostInput(
                post_id=post_id, author=author, link=link,
                raw_text=post_text, image_urls=list(image_urls),
                source="facebook",
            ),
            full_dict=full_dict,
            pattern_dict=pattern_dict,
            weapon_map=weapon_map,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        # 只捕捉已知資料/解析例外；未知例外向上拋出（方便發現程式缺陷）
        v2_error = f"{type(exc).__name__}:{str(exc)[:200]}"

    # ── B. shadow：legacy 為正式，記錄 text V2 與 Vision 差異 ──
    if mode == "shadow":
        legacy_data = legacy_parser(post_text)
        warnings = []
        if v2_error:
            warnings.append(f"v2_error:{v2_error}")
        shadow_diff = _build_shadow_diff(legacy_data, v2_result)
        vision_summary = None
        if vision_inputs:
            vp = _run_vision_merge(post_id, author, link, post_text, image_urls,
                                   vision_inputs, full_dict, pattern_dict,
                                   weapon_map, warnings)
            _extend_shadow_diff_vision(shadow_diff, vp,
                                       input_count=len(vision_inputs))
            warnings.extend(vp.warnings)  # 圖片錯誤等 warning 不影響 legacy，但要記錄
            vision_summary = _build_vision_summary(
                vp, input_count=len(vision_inputs), used=vp.vision_used)
        return ProductionParseResult(
            data=legacy_data, source="shadow_legacy", blocked=False,
            warnings=list(dict.fromkeys(warnings)),
            shadow_diff=shadow_diff, vision_summary=vision_summary,
        )

    # ── C/D. safe / v2_only（有 vision_inputs → Vision merge 優先）──
    if vision_inputs:
        vp = _run_vision_merge(post_id, author, link, post_text, image_urls,
                               vision_inputs, full_dict, pattern_dict,
                               weapon_map, [])
        vp_warnings = list(dict.fromkeys(vp.warnings))
        reasons = vision_merge_safe_reasons(vp)
        if not reasons:
            # Vision merge 安全 → 採用 merged V2
            assert vp.legacy_result is not None and vp.legacy_result.legacy_data is not None
            return ProductionParseResult(
                data=vp.legacy_result.legacy_data, source="v2", blocked=False,
                warnings=list(dict.fromkeys(["vision_merged"] + vp_warnings)),
                vision_summary=_build_vision_summary(
                    vp, input_count=len(vision_inputs), used=True),
                structured_candidates=build_market_candidates(vp.merged_post)
                if vp.merged_post is not None else [],
            )
        # Vision 不安全 → 先試 text-only V2
        text_reasons = _v2_safe_reasons(v2_result) if v2_result is not None else ["v2_error"]
        if not text_reasons:
            assert v2_result is not None
            return ProductionParseResult(
                data=v2_result.legacy_data, source="v2", blocked=False,
                warnings=list(dict.fromkeys(
                    [f"vision_fallback_to_text:{','.join(reasons)}"] + vp_warnings)),
                vision_summary=_build_vision_summary(
                    vp, input_count=len(vision_inputs), used=False,
                    fallback_to_text=True),
                structured_candidates=build_market_candidates(v2_result.parsed_post)
                if v2_result.parsed_post is not None else [],
            )
        # 兩者都不安全
        if mode == "v2_only":
            return ProductionParseResult(
                data=None, source="skipped", blocked=True,
                warnings=list(dict.fromkeys(
                    [f"vision_blocked:{','.join(reasons)}"] + vp_warnings)),
                vision_summary=_build_vision_summary(
                    vp, input_count=len(vision_inputs), used=False),
            )
        # safe：fallback legacy（保留原 ×4.5 行為）
        data = legacy_parser(post_text)
        return ProductionParseResult(
            data=data, source="legacy", blocked=False,
            warnings=list(dict.fromkeys(
                [f"vision_fallback_to_legacy:{','.join(reasons)}"] + vp_warnings)),
            vision_summary=_build_vision_summary(
                vp, input_count=len(vision_inputs), used=False,
                fallback_to_legacy=True),
        )

    # ── 無 vision_inputs：Phase 6.2 原行為 ──
    if v2_result is None:
        if mode == "v2_only":
            return ProductionParseResult(
                data=None, source="skipped", blocked=True,
                warnings=[f"v2_error:{v2_error}"], shadow_diff=None,
            )
        data = legacy_parser(post_text)
        return ProductionParseResult(
            data=data, source="legacy", blocked=False,
            warnings=[f"v2_fallback:v2_error"], shadow_diff=None,
        )

    reasons = _v2_safe_reasons(v2_result)

    if not reasons:
        return ProductionParseResult(
            data=v2_result.legacy_data, source="v2", blocked=False,
            warnings=[], shadow_diff=None,
            structured_candidates=build_market_candidates(v2_result.parsed_post)
            if v2_result.parsed_post is not None else [],
        )

    if mode == "v2_only":
        return ProductionParseResult(
            data=None, source="skipped", blocked=True,
            warnings=[f"v2_blocked:{','.join(reasons)}"], shadow_diff=None,
        )
    data = legacy_parser(post_text)
    return ProductionParseResult(
        data=data, source="legacy", blocked=False,
        warnings=[f"v2_fallback:{','.join(reasons)}"], shadow_diff=None,
    )
