# -*- coding: utf-8 -*-
"""
item_validator.py — V2 Phase P2 Validation Hard Gate

職責（與 parser / market lookup 分離）：
- canonical name 驗證（受信任 catalog / dictionary）
- retry policy（初次 + 最多一次 retry）
- 建立 VerifiedMarketItem（market lookup 的唯一合法輸入）

規則：
- verified 只能由受信任來源產生（trusted_dictionary_exact /
  canonical_catalog / normalized_catalog_alias）
- LLM / Vision / OCR / user_text / fuzzy 只能產生 candidate，
  不得單獨宣稱 verified
- 驗證失敗一律 fail-closed（verified=False + 固定錯誤碼）
- 不保存 credential、endpoint、原始 exception
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum

# 固定錯誤碼 allowlist（不得動態拼字串）
VALIDATION_ERROR_CODES = frozenset({
    "item_validation_empty_name",
    "item_validation_invalid_format",
    "item_validation_catalog_miss",
    "item_validation_retry_failed",
    "item_validation_conflicting_identity",
    "item_validation_service_unavailable",
})

# 允許的 verified_by 來源 allowlist
VERIFIED_BY_SOURCES = frozenset({
    "trusted_dictionary_exact",
    "canonical_catalog",
    "normalized_catalog_alias",
})

# 不得單獨視為 verified 的來源（只能產生 candidate）
CANDIDATE_ONLY_SOURCES = frozenset({
    "llm", "vision", "ocr", "user_text", "fuzzy_match",
    "legacy_first_result",
})


class ValidationStatus(str, Enum):
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"


@dataclass(frozen=True)
class ItemValidationResult:
    """不可變驗證結果。"""

    original_name: str
    canonical_market_hash_name: str | None
    verified: bool
    verified_by: str | None
    validation_error: str | None
    attempts: int
    evidence: str = "catalog_lookup"

    def __post_init__(self) -> None:
        if not isinstance(self.original_name, str):
            raise TypeError("original_name 必須是 str")
        if self.canonical_market_hash_name is not None and \
                not isinstance(self.canonical_market_hash_name, str):
            raise TypeError("canonical_market_hash_name 必須是 str 或 None")
        if not isinstance(self.verified, bool):
            raise TypeError("verified 必須是 bool")
        if self.verified_by is not None and \
                self.verified_by not in VERIFIED_BY_SOURCES:
            raise ValueError(
                f"verified_by 不在 allowlist：{self.verified_by!r}")
        if self.validation_error is not None and \
                self.validation_error not in VALIDATION_ERROR_CODES:
            raise ValueError(
                f"validation_error 不在 allowlist：{self.validation_error!r}")
        if not isinstance(self.attempts, int) or \
                isinstance(self.attempts, bool) or self.attempts < 1:
            raise ValueError("attempts 必須是正整數")
        # 一致性：verified=True → 有 canonical + 無 error + verified_by
        if self.verified:
            if not self.canonical_market_hash_name:
                raise ValueError("verified=True 時 canonical 不可為空")
            if self.validation_error is not None:
                raise ValueError("verified=True 時 validation_error 必須 None")
            if self.verified_by is None:
                raise ValueError("verified=True 時 verified_by 不可為空")
        else:
            if self.validation_error is None:
                raise ValueError("verified=False 時 validation_error 不可為空")


@dataclass(frozen=True)
class VerifiedMarketItem:
    """market lookup 的唯一合法輸入（只有 ItemValidator 可建立）。"""

    market_hash_name: str
    verified_by: str
    source_candidate_index: int | None = None
    validation_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.market_hash_name, str) or \
                not self.market_hash_name.strip():
            raise ValueError("market_hash_name 不可為空")
        if self.verified_by not in VERIFIED_BY_SOURCES:
            raise ValueError(f"verified_by 不在 allowlist：{self.verified_by!r}")
        if self.source_candidate_index is not None and \
                (not isinstance(self.source_candidate_index, int)
                 or isinstance(self.source_candidate_index, bool)
                 or self.source_candidate_index < 0):
            raise ValueError("source_candidate_index 必須是非負 int 或 None")
        if self.validation_digest and (
                not isinstance(self.validation_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.validation_digest)):
            raise ValueError("validation_digest 必須是 64-hex 或空字串")


class ItemValidator:
    """受信任 catalog 驗證器（離線；不發網路、不查 market price）。"""

    def __init__(
        self,
        *,
        dict_path: str | None = None,
        max_attempts: int = 2,
    ) -> None:
        if not isinstance(max_attempts, int) or \
                isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts 必須是正整數")
        if max_attempts > 2:
            raise ValueError("max_attempts 最大為 2（初次 + 一次 retry）")
        self.max_attempts = max_attempts
        if dict_path is None:
            dict_path = os.path.join(
                os.path.dirname(os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__)))),
                "skin_dict.json")
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("item_validator_catalog_unavailable") from exc
        self._full: dict[str, str] = dict(data.get("full_cn_to_en", {}))
        self._pattern: dict[str, str] = dict(
            data.get("pattern_cn_to_en", {}))
        if not self._full:
            raise RuntimeError("item_validator_catalog_empty")
        # P2.1：確定性 catalog 集合（禁止 substring）
        self._norm_full: dict[str, str] = {
            self._normalize(cn): cn for cn in self._full}
        # canonical English 完整商品名集合（full_cn_to_en 的 values：
        # 完整 "Weapon | Skin" 身份；pattern values 只是 skin 名，不加入）
        self._canonical_market_names: set[str] = set()
        self._norm_canonical: dict[str, str] = {}
        for en in self._full.values():
            en = en.strip()
            if en:
                self._canonical_market_names.add(en)
                # P2.3：★ 為 catalog 資料的一部分；同時註冊無 ★ alias，
                # 讓「Sport Gloves | Nocts」這類無 ★ 輸入也可驗證
                self._canonical_market_names.add(en.replace("★ ", ""))
                self._norm_canonical.setdefault(
                    self._normalize(en), en)
                self._norm_canonical.setdefault(
                    self._normalize(en.replace("★ ", "")), en)

    # ---- 正規化 ----
    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"\s+", "", name).strip().lower()

    _WEAR_RE = re.compile(
        r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|"
        r"Battle-Scarred)\)$", re.IGNORECASE)
    _PREFIX_RE = re.compile(r"^(StatTrak™\s*)?", re.IGNORECASE)

    @staticmethod
    def _strip_wear(mhn: str) -> str:
        """剝離磨損後綴（"AK-47 | Redline (Field-Tested)" → base）。"""
        m = re.search(r"^(.*?)\s*\((Factory New|Minimal Wear|"
                      r"Field-Tested|Well-Worn|Battle-Scarred)\)\s*$",
                      mhn.strip(), re.IGNORECASE)
        return m.group(1).strip() if m else mhn.strip()

    def _decompose(self, name: str) -> tuple[str, str, str | None]:
        """拆解 → (identity, prefix, wear)。

        identity = 無 StatTrak™ 前綴、無 wear 的商品名（catalog 比對用；
                   ★ 保留——catalog 資料含 ★）
        prefix   = "StatTrak™ " 前綴（canonical 組裝時保留）
        wear     = 合法磨損或 None（canonical 組裝時保留）
        """
        raw = name.strip()
        m = self._WEAR_RE.search(raw)
        wear = None
        identity = raw
        if m:
            wear = m.group(1)
            identity = raw[: m.start()].strip()
        m2 = self._PREFIX_RE.match(identity)
        prefix = m2.group(0) if m2 else ""
        identity = identity[len(prefix):].strip() if prefix else identity
        return identity, prefix, wear

    @staticmethod
    def _assemble(prefix: str, canonical: str, wear: str | None) -> str:
        """組裝 canonical market name（保留合法前綴與磨損）。

        prefix（StatTrak™）與 canonical 內含 ★ 疊加時統一為
        "★ StatTrak™ <name>" 格式。
        """
        if prefix and canonical.startswith("★ "):
            out = "★ " + prefix.strip() + " " + canonical[2:]
        elif prefix:
            out = prefix + canonical
        else:
            out = canonical
        if wear:
            out = f"{out} ({wear})"
        return out

    def validate_market_name(self, mhn: str) -> bool:
        """canonical English market name 驗證（完整商品身份，無網路）。"""
        if not isinstance(mhn, str) or not mhn.strip():
            return False
        base = self._strip_wear(mhn)
        if base in self._canonical_market_names:
            return True
        norm = self._normalize(base)
        return bool(norm and norm in self._norm_canonical)

    def _lookup(self, raw_name: str) -> tuple[str | None, str | None]:
        """受信任 catalog 查詢（P2.1：禁止 substring/contains）。

        僅接受：
        1. full dictionary key exact equality → trusted_dictionary_exact
        2. full dictionary key normalized full equality → normalized_catalog_alias
        3. canonical English market name exact equality → canonical_catalog
        pattern_dict 不得作為完整商品 catalog。
        """
        name = raw_name.strip()
        if name in self._full:
            return self._full[name], "trusted_dictionary_exact"
        norm = self._normalize(name)
        if norm and norm in self._norm_full:
            return self._full[self._norm_full[norm]], \
                "normalized_catalog_alias"
        # canonical English market name exact（剝離磨損後比對）
        base = self._strip_wear(name)
        if base in self._canonical_market_names:
            return base, "canonical_catalog"
        norm_base = self._normalize(base)
        if norm_base and norm_base in self._norm_canonical:
            return self._norm_canonical[norm_base], "canonical_catalog"
        return None, None

    # ---- 主驗證 ----
    def validate_candidate(
        self,
        name: str,
        *,
        source: str = "user_text",
    ) -> ItemValidationResult:
        """驗證 candidate name。source 只用於診斷，不影響 verified。"""
        if not isinstance(name, str):
            raise TypeError("name 必須是 str")
        if not name.strip():
            return ItemValidationResult(
                original_name=name, canonical_market_hash_name=None,
                verified=False, verified_by=None,
                validation_error="item_validation_empty_name",
                attempts=1)
        if len(name) > 200 or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", name):
            return ItemValidationResult(
                original_name=name, canonical_market_hash_name=None,
                verified=False, verified_by=None,
                validation_error="item_validation_invalid_format",
                attempts=1)

        identity, prefix, wear = self._decompose(name)
        canonical, verified_by = self._lookup(identity)
        if canonical:
            # P2.3：canonical 保留合法 wear / ★ / StatTrak™ 前綴
            out_name = self._assemble(prefix, canonical, wear)
            return ItemValidationResult(
                original_name=name, canonical_market_hash_name=out_name,
                verified=True, verified_by=verified_by,
                validation_error=None, attempts=1)
        # 第一次 miss → retry（attempts=2）
        if self.max_attempts >= 2:
            retry_name = self._retry_variant(name)
            r_identity, r_prefix, r_wear = self._decompose(retry_name)
            canonical2, verified_by2 = self._lookup(r_identity)
            if canonical2:
                return ItemValidationResult(
                    original_name=name,
                    canonical_market_hash_name=self._assemble(
                        r_prefix, canonical2, r_wear),
                    verified=True, verified_by=verified_by2,
                    validation_error=None, attempts=2)
            return ItemValidationResult(
                original_name=name, canonical_market_hash_name=None,
                verified=False, verified_by=None,
                validation_error="item_validation_retry_failed",
                attempts=2)
        return ItemValidationResult(
            original_name=name, canonical_market_hash_name=None,
            verified=False, verified_by=None,
            validation_error="item_validation_catalog_miss", attempts=1)

    def _retry_variant(self, name: str) -> str:
        """retry 變體：移除磨損詞與常見停用詞（保留 | 分隔符）。"""
        cleaned = re.sub(
            r"(崭新出厂|久经沙场|略有磨损|久經沙場|嶄新出廠|略有磨損|"
            r"破损不堪|戰痕累累|战痕累累|factory new|minimal wear|"
            r"field tested|well worn|battle scarred|全新|久經|久经)",
            "", name)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or name


# ================================================================
# Market Lookup 最後防線
# ================================================================
def require_verified_market_item(
    data: dict[str, object] | None,
    *,
    source_candidate_index: int | None = None,
    validator: "ItemValidator | None" = None,
) -> VerifiedMarketItem | None:
    """production market lookup 前的最後防線（P2.1：forged dict 防線）。

    - verified 必須是嚴格 bool True（1/"true"/None 一律拒絕）
    - verified_by 必須在 allowlist
    - market_hash_name 必須是完整商品身份且通過受信任 canonical catalog
      再驗證（不能只相信 caller 的 flags——"Redline"/任意名稱會被拒）
    - validator 未提供時以預設 ItemValidator 驗證
    """
    if not isinstance(data, dict):
        return None
    verified = data.get("verified")
    if verified is not True:  # 嚴格 bool（1/"true"/None 全拒）
        return None
    mhn = data.get("market_hash_name")
    if not isinstance(mhn, str) or not mhn.strip():
        return None
    verified_by = data.get("verified_by")
    if not isinstance(verified_by, str) or \
            verified_by not in VERIFIED_BY_SOURCES:
        return None
    # P2.1：canonical catalog 再驗證（防偽造 dict）
    if validator is None:
        validator = _get_default_validator()
    if not validator.validate_market_name(mhn):
        return None
    digest = hashlib.sha256(mhn.encode("utf-8")).hexdigest()
    return VerifiedMarketItem(
        market_hash_name=mhn.strip(), verified_by=verified_by,
        source_candidate_index=source_candidate_index,
        validation_digest=digest)


# 模組級預設 validator（延遲初始化：ItemValidator 建構需 catalog）
_DEFAULT_VALIDATOR: "ItemValidator | None" = None


def _get_default_validator() -> "ItemValidator":
    global _DEFAULT_VALIDATOR
    if _DEFAULT_VALIDATOR is None:
        _DEFAULT_VALIDATOR = ItemValidator()
    return _DEFAULT_VALIDATOR
