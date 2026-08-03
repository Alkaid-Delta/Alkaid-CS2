# -*- coding: utf-8 -*-
"""test_validation_hard_gate.py — Phase P2 Validation Hard Gate"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


# ================================================================
# B. Production hard-gate tests（先寫 RED：修正前應失敗）
# ================================================================
def test_require_verified_market_item_rejects_unverified():
    """unverified dict → None（gate 必須存在且拒絕）"""
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    assert require_verified_market_item(
        {"market_hash_name": "AK-47 | Redline (Field-Tested)",
         "verified": False,
         "validation_error": "item_validation_retry_failed"}) is None


def test_require_verified_market_item_rejects_truthy_nonbool():
    """1 / "true" / None 不得視為 verified（嚴格 bool）"""
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    for bad in (1, 0, "true", "false", None):
        assert require_verified_market_item(
            {"market_hash_name": "AK-47 | Redline (Field-Tested)",
             "verified": bad}) is None, bad


def test_require_verified_market_item_accepts_verified():
    """verified=True + canonical name → VerifiedMarketItem"""
    from alkaid_cs2.services.item_validator import (
        VerifiedMarketItem, require_verified_market_item,
    )
    vm = require_verified_market_item(
        {"market_hash_name": "AK-47 | Redline (Field-Tested)",
         "verified": True, "verified_by": "trusted_dictionary_exact"})
    assert isinstance(vm, VerifiedMarketItem)
    assert vm.market_hash_name == "AK-47 | Redline (Field-Tested)"


def test_item_validator_exact_dictionary_verified():
    """trusted dictionary exact match → verified"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("AK-47 | 红线", source="user_text")
    assert r.verified is True
    assert r.verified_by == "trusted_dictionary_exact"
    assert r.validation_error is None


def test_item_validator_unknown_name_unresolved():
    """unknown name（max_attempts=2）→ retry_failed"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("神秘皮膚XYZ", source="user_text")
    assert r.verified is False
    assert r.validation_error == "item_validation_retry_failed"
    assert r.attempts == 2


def test_item_validator_single_attempt_catalog_miss():
    """max_attempts=1 → catalog_miss（無 retry）"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator(max_attempts=1)
    r = v.validate_candidate("神秘皮膚XYZ", source="user_text")
    assert r.verified is False
    assert r.validation_error == "item_validation_catalog_miss"
    assert r.attempts == 1


def test_item_validator_retry_once_then_unresolved():
    """retry 最多一次；兩次失敗 → unresolved"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator(max_attempts=2)
    r = v.validate_candidate("AK-47 | 红线", source="llm")  # 字典可命中→先測 retry 用未知
    assert r.attempts in (1, 2)
    r2 = v.validate_candidate("完全不存在ZZZ", source="llm")
    assert r2.verified is False
    assert r2.validation_error == "item_validation_retry_failed"
    assert r2.attempts <= 2


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, contents):
        self._contents = iter(contents)

    def create(self, **kw):
        return _FakeResp(json.dumps(next(self._contents)))


class _FakeChat:
    def __init__(self, contents):
        self.completions = _FakeCompletions(contents)


class _FakeClient:
    def __init__(self, contents):
        self.chat = _FakeChat(contents)


def test_legacy_validation_failure_never_returns_first_name():
    """L594 缺陷：兩次驗證失敗不得回傳第一次未驗證名稱（RED）"""
    import analyze_arbitrage as aa

    orig_client = aa.create_client
    orig_verify = aa._verify_skin_on_csgoskins
    # 第一次與 retry 都回傳未驗證名稱、驗證永遠失敗
    aa.create_client = lambda: _FakeClient([
        {"market_hash_name": "神秘皮膚", "seller_price": 5000,
         "confidence": "medium"},
        {"market_hash_name": "別的皮膚名", "seller_price": 5000,
         "confidence": "medium"},
    ])
    aa._verify_skin_on_csgoskins = lambda mhn: False
    try:
        result = aa.extract_skin_info("售 神秘皮膚 5000")
        # 修正後：不得回傳第一次未驗證名稱（"神秘皮膚"）作為可交易結果
        # 合法結果：None 或 unresolved 結構（verified=False + mhn=None）
        assert result is not None, "應回傳結構化 unresolved 結果"
        assert result.get("verified") is False
        assert result.get("market_hash_name") is None, \
            "回傳第一次未驗證名稱（L594 缺陷）"
        assert result.get("validation_error") == \
            "item_validation_retry_failed"
    finally:
        aa.create_client = orig_client
        aa._verify_skin_on_csgoskins = orig_verify


def test_mode_off_validation_failure_blocks_lookup():
    """mode=off + L594 缺陷輸出（unverified）→ lookup=0（gate 必須作用）"""
    from alkaid_cs2.integration.production_bridge import (
        get_v2_parser_mode,
    )
    import analyze_arbitrage as aa

    calls = {"lookup": 0, "arbitrage": 0, "upload": 0}
    orig_lookup = aa.lookup_buff_price
    orig_arb = aa.analyze_arbitrage
    orig_mode = get_v2_parser_mode()
    orig_extract = aa.extract_skin_info
    try:
        import os as _os
        _os.environ["ALKAID_V2_PARSER_MODE"] = "off"
        # 模擬 L594 缺陷輸出：未驗證名稱（兩次驗證失敗仍回傳）
        aa.extract_skin_info = lambda post_text: {
            "market_hash_name": "神秘皮膚", "seller_price": 5000,
            "verified": False, "validation_error": "item_validation_retry_failed",
        }

        def fake_lookup(mh):
            calls["lookup"] += 1
            return {"price_twd": 10000, "volume": 10,
                    "market_hash_name": mh}

        def fake_arb(post, buff):
            calls["arbitrage"] += 1
            return {"profit": 1}

        aa.lookup_buff_price = fake_lookup
        aa.analyze_arbitrage = fake_arb
        import inspect
        sig = inspect.signature(aa.process_posts)
        kwargs = {}
        if "skip_upload" in sig.parameters:
            kwargs["skip_upload"] = True
        aa.process_posts([{"id": "p2-red-1", "content": "售 神秘皮膚 5000",
                           "author": "t", "link": "l"}], **kwargs)
        assert calls["lookup"] == 0, f"unverified 仍進 lookup: {calls}"
        assert calls["arbitrage"] == 0, f"unverified 仍進 arbitrage: {calls}"
    finally:
        aa.lookup_buff_price = orig_lookup
        aa.analyze_arbitrage = orig_arb
        aa.extract_skin_info = orig_extract
        _os.environ["ALKAID_V2_PARSER_MODE"] = orig_mode


# ================================================================
# B. Production hard-gate（補齊）
# ================================================================
def _run_off_with_extract(fake_extract, *, mode="off"):
    """process_posts + monkeypatch extract_skin_info，回傳 call 統計。"""
    import analyze_arbitrage as aa
    from alkaid_cs2.integration.production_bridge import get_v2_parser_mode
    import os as _os

    calls = {"lookup": 0, "arbitrage": 0, "upload": 0}
    orig_mode = get_v2_parser_mode()
    orig_extract = aa.extract_skin_info
    orig_lookup = aa.lookup_buff_price
    orig_arb = aa.analyze_arbitrage
    orig_upload = aa.upload_to_cloud
    try:
        _os.environ["ALKAID_V2_PARSER_MODE"] = mode
        aa.extract_skin_info = fake_extract
        aa.lookup_buff_price = lambda mh: (
            calls.__setitem__("lookup", calls["lookup"] + 1) or
            {"price_twd": 10000, "volume": 10, "market_hash_name": mh})
        aa.analyze_arbitrage = lambda post, buff: (
            calls.__setitem__("arbitrage", calls["arbitrage"] + 1) or None)
        aa.upload_to_cloud = lambda deal: calls.__setitem__(
            "upload", calls["upload"] + 1)
        import inspect
        sig = inspect.signature(aa.process_posts)
        kwargs = {"skip_upload": True} if "skip_upload" in sig.parameters else {}
        aa.process_posts([{"id": "p2-gate-1", "content": "售 某皮膚 5000",
                           "author": "t", "link": "l"}], **kwargs)
        return calls
    finally:
        aa.extract_skin_info = orig_extract
        aa.lookup_buff_price = orig_lookup
        aa.analyze_arbitrage = orig_arb
        aa.upload_to_cloud = orig_upload
        _os.environ["ALKAID_V2_PARSER_MODE"] = orig_mode


_UNVERIFIED = {"market_hash_name": "神秘皮膚", "seller_price": 5000,
               "verified": False,
               "validation_error": "item_validation_retry_failed"}
_VERIFIED = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
             "seller_price": 5000, "verified": True,
             "verified_by": "trusted_dictionary_exact",
             "confidence": "high", "currency": "TWD"}


# 15. mode=off + validation fail → lookup=0
def test_mode_off_validation_failure_lookup_zero():
    calls = _run_off_with_extract(lambda t: dict(_UNVERIFIED))
    assert calls["lookup"] == 0
    assert calls["arbitrage"] == 0
    assert calls["upload"] == 0


# 16. shadow + V2 blocked + unsafe legacy candidate → lookup=0
def test_shadow_unsafe_legacy_candidate_lookup_zero():
    def fake_extract(t):
        return dict(_UNVERIFIED)  # legacy fallback 回傳未驗證
    calls = _run_off_with_extract(fake_extract, mode="shadow")
    assert calls["lookup"] == 0


# 17. safe mode + validation fail → lookup=0
def test_safe_mode_validation_failure_lookup_zero():
    calls = _run_off_with_extract(lambda t: dict(_UNVERIFIED), mode="safe")
    assert calls["lookup"] == 0


# 18. v2_only + unverified candidate → lookup=0
def test_v2_only_unverified_lookup_zero():
    calls = _run_off_with_extract(lambda t: dict(_UNVERIFIED), mode="v2_only")
    assert calls["lookup"] == 0


# 19. vision candidate unverified → lookup=0
def test_vision_candidate_unverified_lookup_zero():
    calls = _run_off_with_extract(
        lambda t: dict(_UNVERIFIED, validation_error="item_validation_catalog_miss"))
    assert calls["lookup"] == 0


# 20-21. retry exhausted → arbitrage=0 / upload=0（併入 15 已驗證，此處顯式）
def test_retry_exhausted_arbitrage_upload_zero():
    calls = _run_off_with_extract(lambda t: dict(_UNVERIFIED))
    assert calls["arbitrage"] == 0 and calls["upload"] == 0


# 22. validation service unavailable → fail-closed
def test_validator_unavailable_fail_closed(monkeypatch):
    import analyze_arbitrage as aa
    import os as _os

    calls = {"lookup": 0}
    orig_mode = aa.get_v2_parser_mode if hasattr(aa, "get_v2_parser_mode") else None
    orig_lookup = aa.lookup_buff_price
    orig_extract = aa.extract_skin_info
    try:
        _os.environ["ALKAID_V2_PARSER_MODE"] = "off"
        # 模擬驗證服務失敗：extract 拋 RuntimeError → process_posts 不得 crash 洩漏
        aa.extract_skin_info = lambda t: (_ for _ in ()).throw(
            RuntimeError("validator_service_down"))
        aa.lookup_buff_price = lambda mh: (
            calls.__setitem__("lookup", calls["lookup"] + 1) or {})
        import inspect
        sig = inspect.signature(aa.process_posts)
        kwargs = {"skip_upload": True} if "skip_upload" in sig.parameters else {}
        aa.process_posts([{"id": "p2-fc", "content": "售 X 5000",
                           "author": "t", "link": "l"}], **kwargs)
        assert calls["lookup"] == 0
    finally:
        aa.lookup_buff_price = orig_lookup
        aa.extract_skin_info = orig_extract
        _os.environ["ALKAID_V2_PARSER_MODE"] = "off"


# 23. lookup call receives only verified item
def test_lookup_receives_only_verified():
    seen = []

    def fake_extract(t):
        return dict(_VERIFIED)

    def spy_lookup(mh):
        seen.append(mh)
        return {"price_twd": 10000, "volume": 10, "market_hash_name": mh}
    import analyze_arbitrage as aa
    from alkaid_cs2.integration.production_bridge import get_v2_parser_mode
    import os as _os
    orig_mode, orig_extract = get_v2_parser_mode(), aa.extract_skin_info
    orig_lookup, orig_arb = aa.lookup_buff_price, aa.analyze_arbitrage
    orig_upload = aa.upload_to_cloud
    try:
        _os.environ["ALKAID_V2_PARSER_MODE"] = "off"
        aa.extract_skin_info = fake_extract
        aa.lookup_buff_price = spy_lookup
        aa.analyze_arbitrage = lambda post, buff: None
        aa.upload_to_cloud = lambda d: None
        import inspect
        sig = inspect.signature(aa.process_posts)
        kwargs = {"skip_upload": True} if "skip_upload" in sig.parameters else {}
        aa.process_posts([{"id": "p2-v", "content": "售 红线 5000",
                           "author": "t", "link": "l"}], **kwargs)
        assert seen == ["AK-47 | Redline (Field-Tested)"]
    finally:
        aa.extract_skin_info = orig_extract
        aa.lookup_buff_price = orig_lookup
        aa.analyze_arbitrage = orig_arb
        aa.upload_to_cloud = orig_upload
        _os.environ["ALKAID_V2_PARSER_MODE"] = orig_mode


# 25. verified canonical → fake lookup exactly once
def test_verified_lookup_exactly_once():
    calls = _run_off_with_extract(lambda t: dict(_VERIFIED))
    assert calls["lookup"] == 1


# 29. no secret env reads
def test_gate_no_secret_env_reads(monkeypatch):
    import os as _os
    import analyze_arbitrage as aa
    real_getenv = _os.getenv
    real_environ_get = _os.environ.get
    real_getitem = _os.environ.__getitem__
    n = {"v": 0}

    def spy(k, *a):
        if any(x in k.upper() for x in ("KEY", "TOKEN", "COOKIE", "SECRET",
                                        "PASSWORD", "PROXY", "ENDPOINT")):
            n["v"] += 1
        return real_getenv(k, *a) if a else None

    _os.getenv = spy
    _os.environ.get = lambda k, *a: (
        n.__setitem__("v", n["v"] + 1) if any(
            x in k.upper() for x in ("KEY", "TOKEN", "COOKIE", "SECRET",
                                     "PASSWORD", "PROXY", "ENDPOINT"))
        else None) or real_environ_get(k, *a)
    _os.environ.__getitem__ = lambda k: (
        n.__setitem__("v", n["v"] + 1) or real_getitem(k))
    try:
        _run_off_with_extract(lambda t: dict(_UNVERIFIED))
        _run_off_with_extract(lambda t: dict(_VERIFIED))
        assert n["v"] == 0, f"secret env read: {n['v']}"
    finally:
        _os.getenv = real_getenv
        _os.environ.get = real_environ_get
        _os.environ.__getitem__ = real_getitem


# 30. zero external network calls
def test_gate_zero_network(monkeypatch):
    import socket
    import urllib.request
    import http.client
    n = {"v": 0}

    def boom(*a, **k):
        n["v"] += 1
        raise AssertionError("network call")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    _run_off_with_extract(lambda t: dict(_UNVERIFIED))
    _run_off_with_extract(lambda t: dict(_VERIFIED))
    assert n["v"] == 0


# ================================================================
# C. P0 fixture 補強驗證（21 fixtures，8 個 P2 新增）
# ================================================================
def test_p2_fixtures_present():
    import json as _json
    posts = _json.load(open(os.path.join(
        PROJECT_ROOT, "tests", "regression", "fixtures", "posts.json"),
        encoding="utf-8"))
    exp = _json.load(open(os.path.join(
        PROJECT_ROOT, "tests", "regression", "fixtures", "expected.json"),
        encoding="utf-8"))
    ids = {p["id"] for p in posts}
    assert len(posts) >= 21, f"fixtures 數: {len(posts)}"
    for fid in ("p2_unknown_model_item", "p2_retry_succeeds",
                "p2_retry_fails_twice", "p2_trusted_dict_exact",
                "p2_alias_canonical", "p2_vision_only_unverified",
                "p2_safe_fallback_attempted", "p2_validator_unavailable"):
        assert fid in ids, fid
        assert fid in exp, f"expected 缺 {fid}"


def test_p2_fixture_expected_structure():
    import json as _json
    exp = _json.load(open(os.path.join(
        PROJECT_ROOT, "tests", "regression", "fixtures", "expected.json"),
        encoding="utf-8"))
    for fid in ("p2_unknown_model_item", "p2_retry_fails_twice",
                "p2_vision_only_unverified", "p2_safe_fallback_attempted",
                "p2_validator_unavailable"):
        assert exp[fid]["status"] == "unresolved", fid
        assert exp[fid].get("market_hash_name") is None, fid
    for fid in ("p2_retry_succeeds", "p2_trusted_dict_exact",
                "p2_alias_canonical"):
        assert exp[fid]["status"] == "ok", fid
        assert exp[fid]["items"], fid


def test_validation_failure_fixture_no_longer_xfail():
    """L594 修正後，validation_failure_returns_first 必須是正式 pass 測試。"""
    src = open(os.path.join(
        PROJECT_ROOT, "tests", "regression", "test_golden_posts.py"),
        encoding="utf-8").read()
    assert "returns_unverified_first_result" not in src, \
        "xfail 標記仍在（應已轉正式 pass）"


# ================================================================
# 防退化：trusted dictionary candidate 標記 / legacy 透傳 / gate 不寬鬆
# ================================================================
_P2_FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline",
                 "沙漠之鹰 | 东方之谜": "Desert Eagle | Eastern Enigma"}
_P2_PATTERN_DICT = {"红线": "Redline", "紅線": "Redline",
                    "东方之谜": "Eastern Enigma", "夜行衣": "Nocts"}
_P2_WEAPON_MAP = {"AK-47": "AK-47", "沙漠之鹰": "Desert Eagle",
                  "沙鹰": "Desert Eagle"}


def _parse_item(text, **kw):
    from alkaid_cs2.pipeline.parse_pipeline import parse_post
    from alkaid_cs2.domain.raw_post import RawPostInput
    p = parse_post(
        RawPostInput(post_id="p2-reg-1", raw_text=text, source="test"),
        full_dict=_P2_FULL_DICT, pattern_dict=_P2_PATTERN_DICT,
        weapon_map=_P2_WEAPON_MAP)
    assert p.items, f"無 candidate: {text}"
    return p.items[0]


# 1. full dictionary exact → verified
def test_full_dict_candidate_verified():
    it = _parse_item("售 AK-47 | 红线 久经沙场 5000")
    assert it.verified is True
    assert it.verified_by == "trusted_dictionary_exact"
    assert it.validation_error is None


# 2. normalized alias → verified
def test_normalized_alias_candidate_verified():
    it = _parse_item("售 沙漠之鹰 | 东方之谜 崭新出厂 6000")
    assert it.verified is True
    assert it.verified_by in ("trusted_dictionary_exact",
                              "normalized_catalog_alias")
    assert it.validation_error is None


# 4. LLM candidate 保持 unverified（vision_adapter 已 verified=False）
def test_vision_candidate_unverified():
    from alkaid_cs2.adapters.vision_adapter import vision_payload_to_evidence
    ev = vision_payload_to_evidence({
        "kind": "item_list", "items": [
            {"market_hash_name": "AK-47 | Hyper Beast", "wear": "Field-Tested",
             "role": "selling", "price": 5000, "currency": "TWD",
             "confidence": 0.9}]}, image_index=0,
        image_url="inline://p2-vision-1")
    for it in ev.item_candidates:
        assert it.verified is False
        assert it.verified_by is None


# 7. legacy adapter 透傳 verified fields
def test_legacy_adapter_passthrough_verified():
    from alkaid_cs2.pipeline.parse_pipeline import parse_post
    from alkaid_cs2.adapters.legacy_adapter import to_legacy_skin_info
    from alkaid_cs2.domain.raw_post import RawPostInput
    p = parse_post(
        RawPostInput(post_id="p2-passthrough",
                     raw_text="售 AK-47 | 红线 久经沙场 5000",
                     source="test"),
        full_dict=_P2_FULL_DICT, pattern_dict=_P2_PATTERN_DICT,
        weapon_map=_P2_WEAPON_MAP)
    r = to_legacy_skin_info(p)
    assert r.legacy_data is not None
    assert r.legacy_data["verified"] is True
    assert r.legacy_data["verified_by"] in (
        "trusted_dictionary_exact", "normalized_catalog_alias")
    assert r.legacy_data["validation_error"] is None


# 8. legacy adapter 不得自行把 False 變 True
def test_legacy_adapter_never_forges_verified():
    from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus
    from alkaid_cs2.adapters.legacy_adapter import to_legacy_skin_info
    from alkaid_cs2.domain.item_candidate import (
        ItemCandidate, ItemRole, ItemEvidence)
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    from alkaid_cs2.domain.price_candidate import (
        PriceCandidate, PriceSource, PriceType)
    item = ItemCandidate(
        market_hash_name="AK-47 | Hyper Beast (Field-Tested)",
        weapon="AK-47", skin="Hyper Beast", wear="Field-Tested",
        role=ItemRole.SELLING, original_text="x",
        matched_key="x", match_start=0, match_end=1,
        parser="vision", evidence=ItemEvidence.VISION,
        confidence=0.5, score=1.0, verified=False, verified_by=None,
        validation_error="item_validation_catalog_miss")
    p = ParsedPost(
        post_id="x", raw_text="x", image_urls=[], source="test",
        items=[item],
        prices=[PriceCandidate(
            money=Money(5000, Currency.TWD), price_type=PriceType.SELLER_ASK,
            source=PriceSource.TEXT, evidence=PriceSource.TEXT,
            confidence=0.9)],
        link_decisions=[], parse_status=ParseStatus.UNRESOLVED)
    r = to_legacy_skin_info(p)
    # validation_error 非空 → blocked（不得輸出 unverified 名稱）
    assert r.blocked is True
    assert r.legacy_data is None


# 9-10. safe mode trusted dict → lookup=1；unverified → lookup=0
def test_safe_mode_trusted_dict_lookup_once():
    calls = _run_off_with_extract(lambda t: dict(_VERIFIED), mode="safe")
    assert calls["lookup"] == 1


def test_safe_mode_unverified_lookup_zero():
    calls = _run_off_with_extract(lambda t: dict(_UNVERIFIED), mode="safe")
    assert calls["lookup"] == 0


# 12. verified_by 非 allowlist → gate 拒絕
def test_verified_by_not_in_allowlist_rejected():
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    data = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
            "verified": True, "verified_by": "llm"}
    assert require_verified_market_item(data) is None
    data2 = dict(data)
    data2["verified_by"] = "parser"
    assert require_verified_market_item(data2) is None


# 11. caller candidate 不被 adapter 原地修改
def test_caller_candidate_not_mutated():
    from alkaid_cs2.pipeline.parse_pipeline import parse_post
    from alkaid_cs2.adapters.legacy_adapter import to_legacy_skin_info
    from alkaid_cs2.domain.raw_post import RawPostInput
    p = parse_post(
        RawPostInput(post_id="p2-nomut",
                     raw_text="售 AK-47 | 红线 久经沙场 5000",
                     source="test"),
        full_dict=_P2_FULL_DICT, pattern_dict=_P2_PATTERN_DICT,
        weapon_map=_P2_WEAPON_MAP)
    before = (p.items[0].verified, p.items[0].verified_by,
              p.items[0].validation_error)
    to_legacy_skin_info(p)
    after = (p.items[0].verified, p.items[0].verified_by,
             p.items[0].validation_error)
    assert before == after


# ================================================================
# Phase P2.1 — Canonical validation tightening（RED 先行）
# ================================================================
def test_pattern_without_weapon_unverified():
    """pattern 命中但無 weapon → 不得 verified（目前 pattern 全 verified → RED）"""
    it = _parse_item("售 红线 5000")  # pattern 命中、無武器
    assert it.verified is False, f"pattern without weapon 仍 verified: {it.verified}"
    assert it.verified_by is None


def test_substring_pattern_cannot_verify():
    """任意文字包含 pattern key（非完整商品）→ 不得產生 verified candidate"""
    from alkaid_cs2.pipeline.parse_pipeline import parse_post
    from alkaid_cs2.domain.raw_post import RawPostInput
    p = parse_post(
        RawPostInput(post_id="p2-sub-pat", raw_text="售 我的红线收藏品 5000",
                     source="test"),
        full_dict=_P2_FULL_DICT, pattern_dict=_P2_PATTERN_DICT,
        weapon_map=_P2_WEAPON_MAP)
    for it in p.items:
        assert it.verified is False, it.market_hash_name


def test_substring_full_key_cannot_verify():
    """full key 作為子字串（前後有雜字）→ 不得視為 exact 商品命中"""
    from alkaid_cs2.pipeline.parse_pipeline import parse_post
    from alkaid_cs2.domain.raw_post import RawPostInput
    p = parse_post(
        RawPostInput(post_id="p2-sub-full",
                     raw_text="售 半件AK-47 | 红线複製品 5000", source="test"),
        full_dict=_P2_FULL_DICT, pattern_dict=_P2_PATTERN_DICT,
        weapon_map=_P2_WEAPON_MAP)
    for it in p.items:
        assert it.verified_by != "trusted_dictionary_exact", it.market_hash_name


def test_forged_skin_only_dict_rejected():
    """forged dict（verified=True + "Redline" skin-only）→ gate 拒絕"""
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    forged = {"market_hash_name": "Redline", "verified": True,
              "verified_by": "trusted_dictionary_exact"}
    assert require_verified_market_item(forged) is None


def test_forged_unknown_canonical_rejected():
    """forged unknown canonical full name → gate 拒絕"""
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    forged = {"market_hash_name": "AK-47 | Totally Fake Skin (Field-Tested)",
              "verified": True, "verified_by": "trusted_dictionary_exact"}
    assert require_verified_market_item(forged) is None


def test_skin_only_gate_rejected():
    """skin-only 名稱不得進 market lookup"""
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    for skin in ("Redline", "Vulcan", "Nocts"):
        assert require_verified_market_item(
            {"market_hash_name": skin, "verified": True,
             "verified_by": "trusted_dictionary_exact"}) is None, skin


# ================================================================
# Phase P2.1 — 測試矩陣補齊（8-15）
# ================================================================
def test_skin_only_redline_gate_rejected():
    from alkaid_cs2.services.item_validator import (
        require_verified_market_item,
    )
    for skin in ("Redline", "Vulcan", "Nocts"):
        assert require_verified_market_item(
            {"market_hash_name": skin, "verified": True,
             "verified_by": "trusted_dictionary_exact"}) is None


def test_real_trusted_canonical_lookup_once():
    calls = _run_off_with_extract(lambda t: dict(_VERIFIED))
    assert calls["lookup"] == 1


def test_llm_vision_pattern_name_lookup_zero():
    calls = _run_off_with_extract(
        lambda t: dict(_UNVERIFIED,
                       validation_error="item_validation_catalog_miss"))
    assert calls["lookup"] == 0


def test_full_dict_normalized_equality_verified():
    """normalized full equality（去空白）→ normalized_catalog_alias"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("AK-47|红线", source="user_text")
    assert r.verified is True
    assert r.verified_by == "normalized_catalog_alias"


def test_normalized_equality_not_substring():
    """「AK-47 | 红线XYZ」不等於 full key → 不得 verified"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("AK-47 | 红线XYZ", source="user_text")
    assert r.verified is False


def test_pattern_skin_only_validator_unresolved():
    """ItemValidator 對 skin-only pattern 名 → 不得 verified"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    for skin in ("红线", "Redline", "Vulcan"):
        r = v.validate_candidate(skin, source="user_text")
        assert r.verified is False, skin


def test_validate_market_name_rejects_skin_only():
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    assert v.validate_market_name("Redline") is False
    assert v.validate_market_name("AK-47 | Totally Fake (Field-Tested)") is False
    # 真實 canonical 組裝名（含磨損）→ True
    assert v.validate_market_name("AK-47 | Redline (Field-Tested)") is True


# ================================================================
# Phase P2.2 — Legacy validation convergence（RED 先行）
# ================================================================
def _legacy_extract(post_text, *, verify_fn=None, client_contents=None):
    """呼叫 legacy extract_skin_info（可注入 fake client / verify）。"""
    import analyze_arbitrage as aa
    orig_client = aa.create_client
    orig_verify = aa._verify_skin_on_csgoskins
    try:
        if client_contents is not None:
            aa.create_client = lambda: _FakeClient(list(client_contents))
        if verify_fn is not None:
            aa._verify_skin_on_csgoskins = verify_fn
        return aa.extract_skin_info(post_text)
    finally:
        aa.create_client = orig_client
        aa._verify_skin_on_csgoskins = orig_verify


def test_legacy_pattern_no_weapon_not_verified():
    """legacy pattern 命中但無武器 → 不得 verified（目前直接 verified → RED）"""
    r = _legacy_extract("售 红线 5000")
    assert r is not None
    assert r.get("verified") is False, f"pattern 無武器仍 verified: {r}"


def test_legacy_pattern_no_weapon_lookup_zero():
    """mode=off + pattern 無武器 → lookup=0"""
    calls = _run_off_with_extract(
        lambda t: _legacy_extract("售 红线 5000") or dict(_UNVERIFIED))
    assert calls["lookup"] == 0


def test_legacy_pattern_verified_by_not_normalized_alias():
    """legacy pattern 不得 hard-code normalized_catalog_alias（AST）"""
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "analyze_arbitrage.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Dict):
            vals = [v for k, v in zip(node.keys, node.values)
                    if isinstance(k, _ast.Constant) and k.value == "verified_by"]
            for v in vals:
                if isinstance(v, _ast.Constant) and \
                        v.value in ("trusted_dictionary_exact",
                                    "normalized_catalog_alias"):
                    # 允許 ItemValidationResult 來源變數（Name 節點）——
                    # 但 hard-coded Constant 在 extract_skin_info 內即違規
                    raise AssertionError(
                        f"legacy hard-coded verified_by: {v.value}")


def test_legacy_full_substring_not_exact():
    """legacy full substring 命中（半件AK-47 | 红线複製品）不得 trusted exact"""
    r = _legacy_extract("售 半件AK-47 | 红线複製品 5000")
    if r is not None:
        assert r.get("verified_by") != "trusted_dictionary_exact", r


def test_legacy_catalog_missing_fail_closed():
    """catalog 檔案不存在 → fail-closed（不得落入 LLM）"""
    import analyze_arbitrage as aa
    import os as _os
    orig_exists = _os.path.exists
    orig_client = aa.create_client
    calls = {"llm": 0}
    try:
        _os.path.exists = lambda p: False if "skin_dict.json" in p \
            else orig_exists(p)
        aa.create_client = lambda: (
            calls.__setitem__("llm", calls["llm"] + 1) or _FakeClient([
                {"market_hash_name": "AK-47 | Redline (Field-Tested)",
                 "seller_price": 5000, "confidence": "high"}]))
        try:
            aa.extract_skin_info("售 红线 5000")
            raise AssertionError("catalog 缺失未 fail-closed")
        except RuntimeError as exc:
            assert "catalog_unavailable" in str(exc)
        assert calls["llm"] == 0, "catalog 缺失仍落入 LLM"
    finally:
        _os.path.exists = orig_exists
        aa.create_client = orig_client


def test_legacy_catalog_invalid_fail_closed():
    """catalog JSON invalid → fail-closed（不得 except Exception 吞掉）"""
    import json as _json
    import analyze_arbitrage as aa
    import os as _os
    orig_load = _json.load
    orig_exists = _os.path.exists
    orig_client = aa.create_client
    calls = {"llm": 0}
    try:
        def bad_load(fp, *a, **k):
            raise _json.JSONDecodeError("bad", "doc", 0)

        _json.load = bad_load
        _os.path.exists = lambda p: True if "skin_dict.json" in p \
            else orig_exists(p)
        aa.create_client = lambda: (
            calls.__setitem__("llm", calls["llm"] + 1) or _FakeClient([]))
        try:
            aa.extract_skin_info("售 红线 5000")
            raise AssertionError("catalog invalid 未 fail-closed")
        except RuntimeError as exc:
            assert "catalog_unavailable" in str(exc)
        assert calls["llm"] == 0
    finally:
        _json.load = orig_load
        _os.path.exists = orig_exists
        aa.create_client = orig_client


def test_legacy_llm_external_success_still_needs_local_catalog():
    """csgoskins 驗證成功但本地 catalog miss → verified=False"""
    r = _legacy_extract(
        "售 神秘HyperBeast 5000",
        verify_fn=lambda mhn: True,  # 外部驗證成功（初次 + retry）
        client_contents=[
            {"market_hash_name": "AK-47 | Hyper Beast (Field-Tested)",
             "seller_price": 5000, "confidence": "high"},
            {"market_hash_name": "AK-47 | Hyper Beast (Field-Tested)",
             "seller_price": 5000, "confidence": "medium"},
        ])
    assert r is not None
    assert r.get("verified") is False, \
        "外部驗證成功仍直接 verified（未經本地 catalog）"


def test_legacy_no_broad_except_in_dict_block():
    """extract_skin_info dictionary block 不得含 except Exception（AST）"""
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "analyze_arbitrage.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, _ast.FunctionDef)
              and n.name == "extract_skin_info")
    # dictionary block = 函式內第一個 try（skin_dict.json 讀取 + 字典迴圈）
    first_try = next(n for n in fn.body
                     if isinstance(n, _ast.Try))
    for h in first_try.handlers:
        if h.type is None:
            raise AssertionError("bare except in dictionary block")
        if isinstance(h.type, _ast.Name) and \
                h.type.id in ("Exception", "BaseException"):
            raise AssertionError(
                f"broad except in dictionary block: {h.type.id}")
        if isinstance(h.type, _ast.Tuple):
            for elt in h.type.elts:
                if isinstance(elt, _ast.Name) and \
                        elt.id in ("Exception", "BaseException"):
                    raise AssertionError(
                        f"tuple except 含 broad: {elt.id}")


# ================================================================
# Phase P2.3 — Structured result / wear preservation / AST（RED→GREEN）
# ================================================================
def _make_validation_result(**kw):
    from alkaid_cs2.services.item_validator import (
        ItemValidationResult,
    )
    base = dict(original_name="X", canonical_market_hash_name=None,
                verified=False, verified_by=None, validation_error=None,
                attempts=1, evidence="catalog_lookup")
    base.update(kw)
    return ItemValidationResult(**base)


def test_helper_passthrough_trusted_dictionary_exact(monkeypatch):
    """validator 回傳 trusted_dictionary_exact → helper 完整透傳（不得改 canonical_catalog）"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    captured = {}
    def fake_validate(self, name, *, source):
        captured["source"] = source
        return _make_validation_result(
            original_name=name, canonical_market_hash_name="X",
            verified=True, verified_by="trusted_dictionary_exact")
    monkeypatch.setattr(ItemValidator, "validate_candidate", fake_validate)
    r = aa._validate_legacy_candidate("X", source="legacy_dict_full")
    assert r["verified"] is True
    assert r["verified_by"] == "trusted_dictionary_exact"
    assert r["market_hash_name"] == "X"
    assert captured["source"] == "legacy_dict_full"


def test_helper_passthrough_normalized_alias(monkeypatch):
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    def fake_validate(self, name, *, source):
        return _make_validation_result(
            original_name=name, canonical_market_hash_name="Y",
            verified=True, verified_by="normalized_catalog_alias")
    monkeypatch.setattr(ItemValidator, "validate_candidate", fake_validate)
    r = aa._validate_legacy_candidate("Y", source="legacy_dict_pattern")
    assert r["verified_by"] == "normalized_catalog_alias"
    assert r["market_hash_name"] == "Y"


def test_helper_never_upgrades_unverified(monkeypatch):
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    def fake_validate(self, name, *, source):
        return _make_validation_result(
            original_name=name, canonical_market_hash_name=None,
            verified=False, verified_by=None,
            validation_error="item_validation_catalog_miss")
    monkeypatch.setattr(ItemValidator, "validate_candidate", fake_validate)
    r = aa._validate_legacy_candidate("Z", source="legacy_llm")
    assert r["verified"] is False
    assert r["market_hash_name"] is None
    assert r["validation_error"] == "item_validation_catalog_miss"


def test_helper_sources_passed(monkeypatch):
    """4 個 legacy source 全部實際傳入 validate_candidate"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    seen = []
    def fake_validate(self, name, *, source):
        seen.append(source)
        return _make_validation_result(
            canonical_market_hash_name=name, verified=False,
            validation_error="item_validation_catalog_miss")
    monkeypatch.setattr(ItemValidator, "validate_candidate", fake_validate)
    for src in ("legacy_dict_full", "legacy_dict_pattern",
                "legacy_llm", "legacy_llm_retry"):
        aa._validate_legacy_candidate("N", source=src)
    assert seen == ["legacy_dict_full", "legacy_dict_pattern",
                    "legacy_llm", "legacy_llm_retry"]


def test_helper_no_hardcoded_verified_metadata():
    """helper 原始碼不得硬編碼 verified=True / verified_by"""
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "analyze_arbitrage.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, _ast.FunctionDef)
              and n.name == "_validate_legacy_candidate")
    for node in _ast.walk(fn):
        if isinstance(node, _ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, _ast.Constant) and \
                        k.value == "verified" and \
                        isinstance(v, _ast.Constant) and v.value is True:
                    raise AssertionError("helper 硬編碼 verified=True")
                if isinstance(k, _ast.Constant) and \
                        k.value == "verified_by" and \
                        isinstance(v, _ast.Constant):
                    raise AssertionError(
                        f"helper 硬編碼 verified_by: {v.value}")


def test_helper_calls_validate_candidate_not_market_name():
    """AST：helper 呼叫 validate_candidate（不得只用 validate_market_name）"""
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "analyze_arbitrage.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, _ast.FunctionDef)
              and n.name == "_validate_legacy_candidate")
    calls = [n.func.attr for n in _ast.walk(fn)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)]
    assert "validate_candidate" in calls, f"helper 未呼叫 validate_candidate: {calls}"


def test_no_broad_except_in_helper_and_retry():
    """helper / retry local validation 無 broad except（AST）"""
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "analyze_arbitrage.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    for fname in ("_validate_legacy_candidate", "_get_legacy_validator"):
        fn = next(n for n in tree.body
                  if isinstance(n, _ast.FunctionDef) and n.name == fname)
        for node in _ast.walk(fn):
            if isinstance(node, _ast.ExceptHandler):
                if node.type is None or (
                        isinstance(node.type, _ast.Name) and
                        node.type.id in ("Exception", "BaseException")):
                    raise AssertionError(f"{fname} 含 broad except")


def test_canonical_wear_preserved():
    """輸入含合法磨損 → canonical 保留（AK-47 | Redline (Field-Tested)）"""
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("AK-47 | Redline (Field-Tested)",
                             source="user_text")
    assert r.verified is True
    assert r.canonical_market_hash_name == "AK-47 | Redline (Field-Tested)"
    assert r.verified_by == "canonical_catalog"


def test_canonical_stattrak_prefix_preserved():
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("StatTrak™ AK-47 | Redline (Field-Tested)",
                             source="user_text")
    assert r.verified is True, r
    assert r.canonical_market_hash_name == \
        "StatTrak™ AK-47 | Redline (Field-Tested)", r.canonical_market_hash_name


def test_canonical_star_prefix_preserved():
    from alkaid_cs2.services.item_validator import ItemValidator
    v = ItemValidator()
    r = v.validate_candidate("★ Sport Gloves | Vice (Field-Tested)",
                             source="user_text")
    assert r.verified is True, r
    assert r.canonical_market_hash_name == \
        "★ Sport Gloves | Vice (Field-Tested)", r.canonical_market_hash_name


def test_retry_json_invalid_fail_closed():
    """retry JSON invalid → unresolved（retry_failed）不 crash 不吞"""
    import analyze_arbitrage as aa
    orig_verify = aa._verify_skin_on_csgoskins
    orig_client = aa.create_client
    try:
        aa._verify_skin_on_csgoskins = lambda mhn: True
        class BadResp:
            def __init__(self):
                self.choices = [type("C", (), {
                    "message": type("M", (), {"content": "not-json"})})()]
        import json as _json
        class BadCompletions:
            def __init__(self):
                self._n = 0
            def create(self, **kw):
                self._n += 1
                if self._n == 1:
                    # 第一次：有效 JSON（本地 catalog miss → retry）
                    ok = type("R", (), {"choices": [type("C", (), {
                        "message": type("M", (), {"content": _json.dumps({
                            "market_hash_name":
                                "AK-47 | Hyper Beast (Field-Tested)",
                            "seller_price": 5000,
                            "confidence": "high"})})})]})()
                    return ok
                # 第二次（retry）：invalid JSON
                return BadResp()
        class BadChat:
            def __init__(self):
                self.completions = BadCompletions()
        class BadClient:
            def __init__(self):
                self.chat = BadChat()
        aa.create_client = lambda: BadClient()
        r = aa.extract_skin_info("售 神秘HyperBeast 5000")
        assert r is not None, "retry JSON invalid 未走 unresolved"
        assert r.get("verified") is False
        assert r.get("validation_error") == "item_validation_retry_failed"
    finally:
        aa._verify_skin_on_csgoskins = orig_verify
        aa.create_client = orig_client


# ================================================================
# Phase P2.4 — LLM metadata 透傳 / exception boundary / AST
# ================================================================
def _apply(payload, **kw):
    from alkaid_cs2.services.item_validator import ItemValidationResult
    base = dict(original_name="X", canonical_market_hash_name=None,
                verified=False, verified_by=None, validation_error=None,
                attempts=1, evidence="catalog_lookup")
    base.update(kw)
    import analyze_arbitrage as aa
    return aa._apply_validation_result(payload, {
        "market_hash_name": base["canonical_market_hash_name"],
        "verified": base["verified"],
        "verified_by": base["verified_by"],
        "validation_error": base["validation_error"],
        "attempts": base["attempts"],
    })


def test_apply_passthrough_trusted_dictionary_exact():
    out = _apply({"market_hash_name": "LLM原樣", "verified": True,
                  "verified_by": "canonical_catalog", "seller_price": 5000,
                  "confidence": "high"},
                 canonical_market_hash_name="X",
                 verified=True, verified_by="trusted_dictionary_exact")
    assert out["verified_by"] == "trusted_dictionary_exact"
    assert out["market_hash_name"] == "X"
    assert out["seller_price"] == 5000


def test_apply_passthrough_normalized_alias():
    out = _apply({}, canonical_market_hash_name="Y", verified=True,
                 verified_by="normalized_catalog_alias")
    assert out["verified_by"] == "normalized_catalog_alias"


def test_apply_canonical_name_replaces_llm_name():
    out = _apply({"market_hash_name": "AK-47 | Redline (Field-Tested)",
                  "verified": True, "seller_price": 5000},
                 canonical_market_hash_name="★ Sport Gloves | Nocts (Field-Tested)",
                 verified=True, verified_by="canonical_catalog")
    assert out["market_hash_name"] == "★ Sport Gloves | Nocts (Field-Tested)"


def test_apply_forged_verified_overwritten():
    """payload 偽造 verified=True → 被 validator result 覆蓋"""
    out = _apply({"market_hash_name": "Fake", "verified": True,
                  "verified_by": "trusted_dictionary_exact"},
                 canonical_market_hash_name=None, verified=False,
                 verified_by=None, validation_error="item_validation_catalog_miss")
    assert out["verified"] is False
    assert out["verified_by"] is None
    assert out["market_hash_name"] is None
    assert out["validation_error"] == "item_validation_catalog_miss"


def test_apply_never_upgrades_unverified():
    out = _apply({"verified": False}, verified=False, verified_by=None)
    assert out["verified"] is False


def test_apply_attempts_passthrough():
    out = _apply({}, verified=False, verified_by=None, attempts=2)
    assert out["validation_attempts"] == 2


def test_apply_does_not_mutate_payload():
    payload = {"market_hash_name": "P", "verified": False, "seller_price": 1}
    import copy
    snapshot = copy.deepcopy(payload)
    _apply(payload, canonical_market_hash_name="X", verified=True,
           verified_by="canonical_catalog")
    assert payload == snapshot


def test_llm_initial_passthrough_trusted_dict(monkeypatch):
    """LLM 初次：validator 回傳 trusted_dictionary_exact → 最終保留"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    def fake_validate(self, name, *, source):
        return _make_validation_result(
            original_name=name, canonical_market_hash_name="X",
            verified=True, verified_by="trusted_dictionary_exact")
    monkeypatch.setattr(ItemValidator, "validate_candidate", fake_validate)
    monkeypatch.setattr(aa, "_verify_skin_on_csgoskins", lambda mhn: True)
    monkeypatch.setattr(aa, "create_client",
                        lambda: _FakeClient([
                            {"market_hash_name": "LLM名", "seller_price": 5000,
                             "confidence": "high"}]))
    r = aa.extract_skin_info("售 神秘 5000")
    assert r is not None and r.get("verified") is True
    assert r["verified_by"] == "trusted_dictionary_exact"
    assert r["market_hash_name"] == "X"
    assert r["validation_attempts"] == 1


def test_llm_retry_passthrough_canonical(monkeypatch):
    """retry：canonical name 替換 + verified_by 保留"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    def fake_validate(self, name, *, source):
        return _make_validation_result(
            original_name=name,
            canonical_market_hash_name="★ Sport Gloves | Nocts (Field-Tested)",
            verified=True, verified_by="canonical_catalog", attempts=2)
    monkeypatch.setattr(ItemValidator, "validate_candidate", fake_validate)
    monkeypatch.setattr(aa, "_verify_skin_on_csgoskins", lambda mhn: True)
    monkeypatch.setattr(aa, "create_client",
                        lambda: _FakeClient([
                            {"market_hash_name": "神秘商品Q", "seller_price": 5000,
                             "confidence": "high"},
                            {"market_hash_name": "Sport Gloves | Nocts (Field-Tested)",
                             "seller_price": 5000, "confidence": "medium"}]))
    r = aa.extract_skin_info("售 神秘商品Q 5000")
    assert r is not None and r.get("verified") is True
    assert r["verified_by"] == "canonical_catalog"
    assert r["market_hash_name"] == "★ Sport Gloves | Nocts (Field-Tested)"
    assert r["validation_attempts"] == 2


def test_llm_initial_transport_fail_closed(monkeypatch):
    """初次 API transport 例外 → 安全 unresolved（service_unavailable）+ lookup=0"""
    import analyze_arbitrage as aa
    from openai import APIError
    class Boom:
        def create(self, **kw):
            raise APIError("boom", request=None, body=None)
    class BoomChat:
        def __init__(self):
            self.completions = Boom()
    class BoomClient:
        def __init__(self):
            self.chat = BoomChat()
    monkeypatch.setattr(aa, "create_client", lambda: BoomClient())
    monkeypatch.setattr(aa, "_verify_skin_on_csgoskins", lambda mhn: True)
    r = aa.extract_skin_info("售 神秘 5000")
    assert r is not None
    assert r.get("verified") is False
    assert r.get("validation_error") == "item_validation_service_unavailable"


def test_llm_initial_json_invalid_fail_closed(monkeypatch):
    """初次 JSON invalid → 安全 unresolved + lookup=0"""
    import analyze_arbitrage as aa
    class Bad:
        def create(self, **kw):
            return _FakeResp("not-json")
    class BadChat:
        def __init__(self):
            self.completions = Bad()
    class BadClient:
        def __init__(self):
            self.chat = BadChat()
    monkeypatch.setattr(aa, "create_client", lambda: BadClient())
    r = aa.extract_skin_info("售 神秘 5000")
    assert r is not None
    assert r.get("verified") is False
    assert r.get("validation_error") == "item_validation_service_unavailable"


def test_validator_runtimeerror_not_transport(monkeypatch):
    """_validate_legacy_candidate 拋 RuntimeError → 向上傳播（fail-closed）"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    def boom(self, name, *, source):
        raise RuntimeError("item_validator_catalog_unavailable")
    monkeypatch.setattr(ItemValidator, "validate_candidate", boom)
    monkeypatch.setattr(aa, "_verify_skin_on_csgoskins", lambda mhn: True)
    monkeypatch.setattr(aa, "create_client",
                        lambda: _FakeClient([
                            {"market_hash_name": "夜行衣", "seller_price": 5000,
                             "confidence": "high"}]))
    try:
        aa.extract_skin_info("售 夜行衣 5000")
        raise AssertionError("RuntimeError 被吞")
    except RuntimeError as exc:
        assert "catalog_unavailable" in str(exc)


def test_validator_valueerror_not_swallowed(monkeypatch):
    """_validate_legacy_candidate 拋 ValueError → 不得吞掉"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import ItemValidator
    def boom(self, name, *, source):
        raise ValueError("bad input")
    monkeypatch.setattr(ItemValidator, "validate_candidate", boom)
    monkeypatch.setattr(aa, "_verify_skin_on_csgoskins", lambda mhn: True)
    monkeypatch.setattr(aa, "create_client",
                        lambda: _FakeClient([
                            {"market_hash_name": "夜行衣", "seller_price": 5000,
                             "confidence": "high"}]))
    try:
        aa.extract_skin_info("售 夜行衣 5000")
        raise AssertionError("ValueError 被吞")
    except ValueError:
        pass


def test_ast_p24_boundaries_no_broad_except():
    """P2.4 函式無 broad except（AST）"""
    import ast as _ast
    src = open(os.path.join(PROJECT_ROOT, "analyze_arbitrage.py"),
               encoding="utf-8").read()
    tree = _ast.parse(src)
    for fname in ("_validate_legacy_candidate", "_apply_validation_result",
                  "_call_legacy_llm_json"):
        fn = next(n for n in tree.body
                  if isinstance(n, _ast.FunctionDef) and n.name == fname)
        for node in _ast.walk(fn):
            if isinstance(node, _ast.ExceptHandler):
                if node.type is None:
                    raise AssertionError(f"{fname} bare except")
                if isinstance(node.type, _ast.Name) and \
                        node.type.id in ("Exception", "BaseException"):
                    raise AssertionError(f"{fname} broad except")


def test_ast_p24_no_hardcoded_canonical_in_llm_path():
    """LLM 成功路徑不得硬編碼 verified=True / canonical_catalog（AST）"""
    import ast as _ast
    src = open(os.path.join(PROJECT_ROOT, "analyze_arbitrage.py"),
               encoding="utf-8").read()
    tree = _ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, _ast.FunctionDef)
              and n.name == "extract_skin_info")
    for node in _ast.walk(fn):
        if isinstance(node, _ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, _ast.Constant) and k.value == "verified_by":
                    if isinstance(v, _ast.Constant) and \
                            v.value == "canonical_catalog":
                        raise AssertionError(
                            "LLM 路徑硬編碼 canonical_catalog")
