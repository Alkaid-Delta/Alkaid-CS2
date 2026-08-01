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
import math
import os
from dataclasses import dataclass, field
from typing import Callable

from alkaid_cs2.adapters.legacy_adapter import LegacyAdapterResult, parse_to_legacy
from alkaid_cs2.domain.raw_post import RawPostInput

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
        self.warnings = list(self.warnings)  # defensive copy
        if self.shadow_diff is not None:
            self.shadow_diff = dict(self.shadow_diff)


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

    def summary(self) -> str:
        return (f"V2 metrics: total={self.total} v2={self.v2_used} legacy={self.legacy_used} "
                f"shadow={self.shadow_runs} skipped={self.skipped} "
                f"v2_blocked={self.v2_blocked} v2_fallback={self.v2_fallback} "
                f"name_mismatch={self.name_mismatch} price_mismatch={self.price_mismatch}")


# 全域指標實例（production 統計用）
_METRICS = ProductionParseMetrics()


# ============================================================
# seller_price 防守
# ============================================================
def is_valid_legacy_seller_price(value: object) -> bool:
    """int/float、拒絕 bool、finite、> 0。"""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return value > 0


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
# 主橋接
# ============================================================
def parse_post_for_production(
    *,
    post_id: str,
    author: str,
    link: str,
    post_text: str,
    image_urls: list[str],
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

    # ── A. off：完全 legacy ──
    if mode == "off":
        data = legacy_parser(post_text)
        return ProductionParseResult(
            data=data, source="legacy", blocked=False, warnings=[], shadow_diff=None,
        )

    # ── 執行 V2（shadow/safe/v2_only 都需要）──
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
    except Exception as exc:  # V2 失敗不影響 legacy；記錄錯誤
        v2_error = str(exc)

    # ── B. shadow：legacy 為正式，記錄差異 ──
    if mode == "shadow":
        legacy_data = legacy_parser(post_text)
        warnings = []
        if v2_error:
            warnings.append(f"v2_error:{v2_error}")
        shadow_diff = _build_shadow_diff(legacy_data, v2_result)
        return ProductionParseResult(
            data=legacy_data, source="shadow_legacy", blocked=False,
            warnings=warnings, shadow_diff=shadow_diff,
        )

    # ── C/D. safe / v2_only ──
    if v2_result is None:
        # V2 執行失敗
        if mode == "v2_only":
            return ProductionParseResult(
                data=None, source="skipped", blocked=True,
                warnings=[f"v2_error:{v2_error}"], shadow_diff=None,
            )
        # safe：fallback legacy（legacy raise 不得 silent pass）
        data = legacy_parser(post_text)
        return ProductionParseResult(
            data=data, source="legacy", blocked=False,
            warnings=[f"v2_fallback:v2_error"], shadow_diff=None,
        )

    reasons = _v2_safe_reasons(v2_result)

    if not reasons:
        # 安全：採用 V2
        return ProductionParseResult(
            data=v2_result.legacy_data, source="v2", blocked=False,
            warnings=[], shadow_diff=None,
        )

    # 不安全
    if mode == "v2_only":
        return ProductionParseResult(
            data=None, source="skipped", blocked=True,
            warnings=[f"v2_blocked:{','.join(reasons)}"], shadow_diff=None,
        )
    # safe：fallback legacy
    data = legacy_parser(post_text)
    return ProductionParseResult(
        data=data, source="legacy", blocked=False,
        warnings=[f"v2_fallback:{','.join(reasons)}"], shadow_diff=None,
    )
