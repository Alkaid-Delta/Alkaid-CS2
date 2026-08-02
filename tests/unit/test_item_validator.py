# -*- coding: utf-8 -*-
"""test_item_validator.py — Phase P2 ItemValidator unit tests"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.services.item_validator import (  # noqa: E402
    VALIDATION_ERROR_CODES,
    VERIFIED_BY_SOURCES,
    CANDIDATE_ONLY_SOURCES,
    ValidationStatus,
    ItemValidationResult,
    ItemValidator,
    VerifiedMarketItem,
    require_verified_market_item,
)


def _v(**kw):
    return ItemValidator(**kw)


# A.1 exact trusted dictionary → verified
def test_exact_dictionary_verified():
    r = _v().validate_candidate("AK-47 | 红线", source="user_text")
    assert r.verified is True
    assert r.verified_by == "trusted_dictionary_exact"
    assert r.validation_error is None
    assert r.canonical_market_hash_name


# A.2 normalized alias → verified
def test_normalized_alias_verified():
    # 去空白 alias（"AK-47 | 红线" 的變體）
    r = _v().validate_candidate("AK-47|红线", source="user_text")
    assert r.verified is True
    assert r.verified_by in ("trusted_dictionary_exact",
                             "normalized_catalog_alias")


# A.3 unknown name → unresolved
def test_unknown_name_unresolved():
    r = _v().validate_candidate("完全不存在之皮膚XYZ", source="user_text")
    assert r.verified is False
    assert r.validation_error == "item_validation_retry_failed"


# A.4 empty name → invalid
def test_empty_name_invalid():
    r = _v().validate_candidate("   ", source="user_text")
    assert r.verified is False
    assert r.validation_error == "item_validation_empty_name"
    assert r.attempts == 1


# A.5 first fail、retry success → verified
def test_retry_success_verified():
    # 中文名含磨損詞（retry 變體移除後命中）——用真實字典名測 retry 路徑
    r = _v().validate_candidate("AK-47 | 红线 久经沙场", source="user_text")
    assert r.verified is True
    assert r.attempts in (1, 2)


# A.6 first and retry fail → unresolved
def test_retry_fail_unresolved():
    r = _v().validate_candidate("神秘皮膚XYZ不存在", source="user_text")
    assert r.verified is False
    assert r.validation_error == "item_validation_retry_failed"
    assert r.attempts == 2


# A.7 retry 次數不超過一次
def test_retry_attempts_bounded():
    r = _v().validate_candidate("神秘皮膚XYZ不存在", source="user_text")
    assert r.attempts <= 2
    # max_attempts 上限強制
    try:
        ItemValidator(max_attempts=3)
        raise AssertionError("max_attempts=3 應被拒絕")
    except ValueError:
        pass


# A.8 LLM name 未經 catalog → not verified
def test_llm_name_not_verified_without_catalog():
    r = _v().validate_candidate("AK-47 | Hyper Beast", source="llm")
    # 英文名不在中文 catalog → 未驗證（除非字典恰有對應）
    assert r.verified is False


# A.9 Vision name 未經 catalog → not verified
def test_vision_name_not_verified_without_catalog():
    r = _v().validate_candidate("weird vision ocr name 12345",
                                source="vision")
    assert r.verified is False


# A.10 invalid verified bool type 拒絕
def test_validation_result_rejects_nonbool_verified():
    try:
        ItemValidationResult(
            original_name="x", canonical_market_hash_name=None,
            verified=1, verified_by=None,
            validation_error="item_validation_empty_name", attempts=1)
        raise AssertionError("verified=1 應被拒絕")
    except TypeError:
        pass


# A.11 verified_by 非 allowlist 拒絕
def test_verified_by_allowlist_enforced():
    try:
        ItemValidationResult(
            original_name="x", canonical_market_hash_name="AK-47 | Redline",
            verified=True, verified_by="llm",
            validation_error=None, attempts=1)
        raise AssertionError("verified_by=llm 應被拒絕")
    except ValueError:
        pass
    assert "llm" not in VERIFIED_BY_SOURCES
    assert "vision" not in VERIFIED_BY_SOURCES
    assert "ocr" not in VERIFIED_BY_SOURCES
    assert "legacy_first_result" not in VERIFIED_BY_SOURCES
    for c in CANDIDATE_ONLY_SOURCES:
        assert c not in VERIFIED_BY_SOURCES


# A.12 validation_error consistency
def test_validation_error_consistency():
    r = _v().validate_candidate("AK-47 | 红线", source="user_text")
    assert r.verified is True and r.validation_error is None
    r2 = _v().validate_candidate("", source="user_text")
    assert r2.verified is False and r2.validation_error is not None
    # verified=False 無 error → 拒絕
    try:
        ItemValidationResult(
            original_name="x", canonical_market_hash_name=None,
            verified=False, verified_by=None, validation_error=None,
            attempts=1)
        raise AssertionError("verified=False 無 error 應被拒絕")
    except ValueError:
        pass


# A.13 deterministic output
def test_deterministic_output():
    a = _v().validate_candidate("AK-47 | 红线 久经沙场", source="user_text")
    b = _v().validate_candidate("AK-47 | 红线 久经沙场", source="user_text")
    assert a.canonical_market_hash_name == b.canonical_market_hash_name
    assert a.verified_by == b.verified_by
    assert a.attempts == b.attempts


# A.14 no network in unit validator
def test_no_network_in_validator(monkeypatch):
    import socket
    import urllib.request
    import http.client
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("network call")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    v = _v()
    v.validate_candidate("AK-47 | 红线", source="user_text")
    v.validate_candidate("神秘XYZ", source="user_text")
    assert calls["n"] == 0


# ---- VerifiedMarketItem / gate ----
def test_verified_market_item_creation():
    vm = VerifiedMarketItem(
        market_hash_name="AK-47 | Redline (Field-Tested)",
        verified_by="trusted_dictionary_exact")
    assert vm.validation_digest == "" or len(vm.validation_digest) == 64


def test_verified_market_item_rejects_bad_verified_by():
    try:
        VerifiedMarketItem(
            market_hash_name="x", verified_by="llm")
        raise AssertionError("verified_by=llm 應被拒絕")
    except ValueError:
        pass


def test_require_verified_gate_strict_bool():
    data = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
            "verified": True, "verified_by": "trusted_dictionary_exact"}
    assert require_verified_market_item(data) is not None
    for bad in (1, 0, "true", "false", None, [], {}):
        d = dict(data)
        d["verified"] = bad
        assert require_verified_market_item(d) is None, bad


def test_require_verified_gate_requires_canonical():
    data = {"market_hash_name": "", "verified": True,
            "verified_by": "trusted_dictionary_exact"}
    assert require_verified_market_item(data) is None
    data2 = {"verified": True, "verified_by": "trusted_dictionary_exact"}
    assert require_verified_market_item(data2) is None


def test_error_codes_allowlist_fixed():
    assert "item_validation_retry_failed" in VALIDATION_ERROR_CODES
    assert "item_validation_catalog_miss" in VALIDATION_ERROR_CODES
    assert "item_validation_empty_name" in VALIDATION_ERROR_CODES
    assert len(VALIDATION_ERROR_CODES) >= 6
