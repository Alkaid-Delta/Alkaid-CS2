"""
test_golden_posts.py — Phase 0 golden regression tests
=======================================================
Runs the CURRENT production parser against hand-verified fixtures.

Known defects are marked xfail (expected failure). Tests are NEVER made to pass
by modifying production code.

Fixtures needing DEEPSEEK_API_KEY are skipped when the key is absent.
Fixtures needing the crawler (Playwright+FB) are skipped in unit runs.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import analyze_arbitrage as aa  # noqa: E402
from tests.regression.legacy_adapter import extract_legacy, parse_market_hash  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

with open(os.path.join(FIXTURES_DIR, "posts.json"), encoding="utf-8") as f:
    POSTS = json.load(f)
with open(os.path.join(FIXTURES_DIR, "expected.json"), encoding="utf-8") as f:
    EXPECTED = json.load(f)


# ---------------------------------------------------------------
# Mock OpenAI client（不依賴真實 API）
# ---------------------------------------------------------------
class FakeCompletions:
    """依呼叫次數依序回傳 JSON 內容"""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    def create(self, **kwargs):
        content = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeChat:
    def __init__(self, responses: list[str]):
        self.completions = FakeCompletions(responses)


class FakeClient:
    def __init__(self, responses: list[str]):
        self.chat = FakeChat(responses)


def _fake_json(mhn: str, price: int = 5000, conf: str = "high") -> str:
    return json.dumps(
        {"market_hash_name": mhn, "seller_price": price, "confidence": conf},
        ensure_ascii=False,
    )


def _verify_fn_fail(_mhn: str) -> bool:
    """模擬驗證永遠失敗"""
    return False


def _get_fixture(fid: str) -> dict:
    return next(p for p in POSTS if p["id"] == fid)


def _extract(fixture: dict, verify_fn=None) -> dict:
    return extract_legacy(fixture["text"], verify_fn=verify_fn)


# ---------------------------------------------------------------
# 1. 簡單單一物品（TWD 價格）
# ---------------------------------------------------------------
def test_simple_single_twd():
    fix = _get_fixture("simple_single_twd")
    result = _extract(fix)
    exp = EXPECTED["simple_single_twd"]

    assert result["status"] == exp["status"], f"status={result['status']}"
    mh = result["market_hash_name"] or ""
    parts = parse_market_hash(mh)
    assert parts["skin"] == exp["items"][0]["skin"], f"skin={parts['skin']}"
    assert parts["wear"] == exp["items"][0]["wear"], f"wear={parts['wear']}"
    assert result["seller_price"] == exp["items"][0]["seller_price"]


# ---------------------------------------------------------------
# 2. 舊流程單商品（夜行衣 → Nocts）
# ---------------------------------------------------------------
def test_legacy_single_nocts():
    fix = _get_fixture("legacy_single_nocts")
    result = _extract(fix)
    exp = EXPECTED["legacy_single_nocts"]

    assert result["status"] == exp["status"]
    mh = result["market_hash_name"] or ""
    assert "Nocts" in mh, f"mhn={mh}"
    assert result["seller_price"] == exp["items"][0]["seller_price"]


# ---------------------------------------------------------------
# 3. 紅線 + 火神同文（已知缺陷：第一命中 return，只回一個）
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: first_match_return — 字典第一命中即 return，無法回多商品",
                   strict=False)
def test_redline_vulcan_simplified():
    fix = _get_fixture("redline_vulcan_simplified")
    result = _extract(fix)
    exp = EXPECTED["redline_vulcan_simplified"]

    # 期望: 至少要能產出 2 個候選（紅線 7480 + 火神 14000）
    assert result["status"] == exp["status"]  # partial 可接受
    # legacy 只有單一回傳 → 無法同時含 Redline 與 Vulcan → 必然 xfail
    mh = result["market_hash_name"] or ""
    parts = parse_market_hash(mh)
    assert parts["skin"] in ("Redline", "Vulcan"), f"unexpected skin={parts['skin']}"


# ---------------------------------------------------------------
# 4. 紅線(繁體) + 火神同文（已知缺陷：繁中「紅線」字典 miss）
#    斷言嚴格要求 Redline：實際 legacy 只會命中「火神」→ 標記缺陷
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: traditional_variant_missing — 紅線(繁)不在 pattern_cn_to_en，貼文只命中火神",
                   strict=False)
def test_redline_vulcan_traditional():
    fix = _get_fixture("redline_vulcan_traditional")
    result = _extract(fix)
    exp = EXPECTED["redline_vulcan_traditional"]

    mh = result["market_hash_name"] or ""
    parts = parse_market_hash(mh)
    # 缺陷：繁體「紅線」miss → 系統應輸出 Redline 卻輸出 Vulcan（或反之）
    assert parts["skin"] == "Redline", f"skin={parts['skin']} (繁中紅線 miss → 只命中火神)"


# ---------------------------------------------------------------
# 5. 售價 + BUFF 底價共存（typed prices 期望，legacy 不支援 → XFAIL）
#    期望: 2100 RMB=buff_floor, 9200 TWD=calculated, 5000 TWD=seller_ask
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: price_role_not_distinguished — legacy 無 typed price 支援",
                   strict=False)
def test_seller_ask_plus_buff_floor():
    fix = _get_fixture("seller_ask_plus_buff_floor")
    result = _extract(fix)
    exp = EXPECTED["seller_ask_plus_buff_floor"]

    # 期望: typed prices 必須存在且含 3 種 type（legacy 無法產出 → 缺陷）
    assert result.get("prices") is not None, "legacy 無法產出 typed prices"
    types = {p["price_type"] for p in result["prices"]}
    assert {"buff_floor", "calculated", "seller_ask"} <= types
    # 名稱仍應正確
    mh = result["market_hash_name"] or ""
    assert "Nocts" in mh, f"mhn={mh}"


# ---------------------------------------------------------------
# 6. RMB 價格（已知缺陷：字典命中路徑無 currency，當 TWD）
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: currency_lost_on_dict_hit — RMB 未被標記，未經 CurrencyService",
                   strict=False)
def test_rmb_price_no_conversion_marker():
    fix = _get_fixture("rmb_price_no_conversion_marker")
    result = _extract(fix)
    exp = EXPECTED["rmb_price_no_conversion_marker"]

    mh = result["market_hash_name"] or ""
    assert "Printstream" in mh
    # 期望 currency=RMB（legacy 無法提供 → 缺陷）
    assert result["currency"] == exp["items"][0]["currency"]


# ---------------------------------------------------------------
# 7. 驗證失敗（L562 缺陷）— mock 驗證，不依賴真實 API
#    第一次回 Fake Skin A、第二次回 Fake Skin B、驗證都 False
#    → 現行 legacy 錯誤回傳第一次結果 → XFAIL
#    Phase 2 validation gate 修正後才改 PASS
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: returns_unverified_first_result — 驗證兩次失敗仍回傳第一次名稱 (L562)",
                   strict=False)
def test_validation_failure_returns_first(monkeypatch):
    fix = _get_fixture("validation_failure_returns_first")

    fake = FakeClient([
        _fake_json("Fake Skin A", 5000, "high"),   # 第一次: 不存在名稱
        _fake_json("Fake Skin B", 5000, "medium"), # 第二次: 也不存在
    ])
    monkeypatch.setattr(aa, "create_client", lambda: fake)
    # verify 固定 False（monkeypatch 由 adapter 內部覆寫 _verify_skin_on_csgoskins）

    result = extract_legacy(fix["text"], verify_fn=_verify_fn_fail)
    exp = EXPECTED["validation_failure_returns_first"]

    # 期望: unresolved（兩次驗證失敗不得回傳未驗證名稱）
    # 現行 L562 缺陷: 回傳第一次 Fake Skin A → 此斷言失敗 → XFAIL
    assert result["status"] == exp["status"], f"status={result['status']} mhn={result['market_hash_name']}"
    assert result["market_hash_name"] is None


# ---------------------------------------------------------------
# 8. 多圖第二張含價格（已知缺陷：crawler 第一張成功就 break）
# ---------------------------------------------------------------
@pytest.mark.skip(reason="requires_crawler: 需要 Playwright+FB 環境，Phase 0 不模擬 crawler")
def test_multi_image_second_has_price():
    fix = _get_fixture("multi_image_second_has_price")
    # 此案例驗證 crawler 的「第一張圖 break」缺陷：
    # 圖1 無價格成功 → break → 圖2 的價格永遠不會被合併
    # 需 crawler 環境實測，unit 階段 skip
    assert fix["known_defect"] == "first_image_break"


# ---------------------------------------------------------------
# 9. StatTrak 暗金（期望通過）
# ---------------------------------------------------------------
def test_stat_trak_ak():
    fix = _get_fixture("stat_trak_ak")
    result = _extract(fix)
    exp = EXPECTED["stat_trak_ak"]

    assert result["status"] == exp["status"]
    mh = result["market_hash_name"] or ""
    parts = parse_market_hash(mh)
    assert parts["skin"] == exp["items"][0]["skin"]
    assert parts["stattrak"] is True, f"stattrak={parts['stattrak']} mhn={mh}"
    assert result["seller_price"] == exp["items"][0]["seller_price"]


# ---------------------------------------------------------------
# 10. 刀類 ★ 前綴（期望通過）
# ---------------------------------------------------------------
def test_knife_star_prefix():
    fix = _get_fixture("knife_star_prefix")
    result = _extract(fix)
    exp = EXPECTED["knife_star_prefix"]

    assert result["status"] == exp["status"]
    mh = result["market_hash_name"] or ""
    parts = parse_market_hash(mh)
    assert parts["skin"] == exp["items"][0]["skin"]
    assert parts["wear"] == exp["items"][0]["wear"]
    assert parts["star"] is True, f"star missing in {mh}"
    assert result["seller_price"] == exp["items"][0]["seller_price"]


# ---------------------------------------------------------------
# 收集所有測試結果供 report.py 使用（額外 fixture 若未定義測試則列入 unsupported）
# ---------------------------------------------------------------
ALL_FIXTURE_IDS = [p["id"] for p in POSTS]
TESTED_FIXTURE_IDS = [
    "simple_single_twd", "legacy_single_nocts",
    "redline_vulcan_simplified", "redline_vulcan_traditional",
    "seller_ask_plus_buff_floor", "rmb_price_no_conversion_marker",
    "validation_failure_returns_first", "multi_image_second_has_price",
    "stat_trak_ak", "knife_star_prefix",
    "buying_post_nocts", "trade_only_post", "no_price_selling_post",
]


# ---------------------------------------------------------------
# 11. 求購文（收）— legacy 無 role 概念 → XFAIL
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: role_not_distinguished — legacy 無 ItemRole，求購文被當 selling",
                   strict=False)
def test_buying_post_nocts():
    fix = _get_fixture("buying_post_nocts")
    result = _extract(fix)
    exp = EXPECTED["buying_post_nocts"]

    # 期望 role=buying（legacy 無 role 欄位 → 缺陷）
    assert result.get("role") == "buying", f"role={result.get('role')}"
    # 名稱仍應解析正確
    mh = result["market_hash_name"] or ""
    assert "Nocts" in mh, f"mhn={mh}"


# ---------------------------------------------------------------
# 12. 純交換文（換/貼）— 不得當 selling → XFAIL
# ---------------------------------------------------------------
@pytest.mark.xfail(reason="known_defect: role_not_distinguished — legacy 無 ItemRole，交換文被當 selling",
                   strict=False)
def test_trade_only_post():
    fix = _get_fixture("trade_only_post")
    result = _extract(fix)
    exp = EXPECTED["trade_only_post"]

    # 期望: 交換文不得產出 selling item（legacy 第一命中回傳 dict → 缺陷）
    assert result["status"] == "unresolved", f"status={result['status']}"


# ---------------------------------------------------------------
# 13. 無價格出售文 — 商品可解析，seller_price 為 null/-1 → PASS
# ---------------------------------------------------------------
def test_no_price_selling_post():
    fix = _get_fixture("no_price_selling_post")
    result = _extract(fix)
    exp = EXPECTED["no_price_selling_post"]

    # 名稱解析正確
    mh = result["market_hash_name"] or ""
    parts = parse_market_hash(mh)
    assert parts["skin"] == exp["items"][0]["skin"]
    # 無價格 → seller_price 必須是 -1（legacy 表示）或 None
    assert result["seller_price"] < 0, f"seller_price={result['seller_price']}"
