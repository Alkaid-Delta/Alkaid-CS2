"""
test_controlled_integration.py — process_posts 受控整合測試（Phase 6.2）

驗證正式 process_posts() 在 feature flag 控制下的行為：
- off 與現行行為一致
- safe 安全單商品走 V2、其餘 fallback
- v2_only blocked 不 fallback
- seller_price=None 不 TypeError
- V2 結果不再 ×4.5；legacy 保留原 ×4.5
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import analyze_arbitrage as aa  # noqa: E402

LEGACY_OK = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
             "seller_price": 1000, "confidence": "high"}


def make_post(text, currency=None, post_id="p1"):
    p = {"id": post_id, "author": "A", "url": "http://x", "content": text, "images": []}
    if currency:
        p["currency"] = currency
    return p


@pytest.fixture
def env(monkeypatch):
    """stub 所有外部依賴（DB / 雲端 / 狀態 / 套利）。"""
    monkeypatch.delenv("ALKAID_V2_PARSER_MODE", raising=False)
    monkeypatch.setattr(aa, "load_state", lambda: {})
    monkeypatch.setattr(aa, "mark_processed", lambda ids, state: None)
    monkeypatch.setattr(aa, "save_state", lambda state: None)
    monkeypatch.setattr(aa, "lookup_buff_price", lambda mh: {"price_twd": 10000, "volume": 10})
    monkeypatch.setattr(aa, "analyze_arbitrage", lambda post, buff: None)
    monkeypatch.setattr(aa, "upload_to_cloud", lambda deal: None)
    monkeypatch.setattr(aa, "save_deal_to_history", lambda deal: None)
    monkeypatch.setattr(aa, "print_deal_report", lambda deal: None)
    return monkeypatch


# ================================================================
# 1. off 模式：legacy 行為不變
# ================================================================
def test_process_posts_off_mode_legacy_unchanged(env):
    spy_calls = []

    def spy(text):
        spy_calls.append(text)
        return dict(LEGACY_OK)

    env.setattr(aa, "extract_skin_info", spy)
    post = make_post("售 AK-47 | 红线 久经沙场 5000")
    deals = aa.process_posts([post])
    assert spy_calls == ["售 AK-47 | 红线 久经沙场 5000"]
    assert post["_seller_price"] == 1000
    assert deals == []


# ================================================================
# 2. safe 模式：安全單商品走 V2
# ================================================================
def test_process_posts_safe_mode_v2_single_item(env):
    env.setenv("ALKAID_V2_PARSER_MODE", "safe")
    post = make_post("售 AK-47 | 红线 久经沙场 算5000")
    deals = aa.process_posts([post])
    assert post["_seller_price"] == 5000
    assert deals == []


# ================================================================
# 3. safe 模式：V2 blocked → fallback legacy
# ================================================================
def test_process_posts_safe_mode_fallback(env):
    env.setenv("ALKAID_V2_PARSER_MODE", "safe")
    spy_calls = []

    def spy(text):
        spy_calls.append(text)
        return dict(LEGACY_OK)

    env.setattr(aa, "extract_skin_info", spy)
    post = make_post("紅線 火神 14000 7480")  # 多商品 → V2 blocked
    aa.process_posts([post])
    assert spy_calls, "V2 blocked 應 fallback legacy"
    assert post["_seller_price"] == 1000


# ================================================================
# 4. v2_only：blocked → skip，不呼叫 legacy
# ================================================================
def test_process_posts_v2_only_skip_blocked(env):
    env.setenv("ALKAID_V2_PARSER_MODE", "v2_only")
    spy_calls = []

    def spy(text):
        spy_calls.append(text)
        return dict(LEGACY_OK)

    env.setattr(aa, "extract_skin_info", spy)
    buff_calls = []
    orig_lookup = aa.lookup_buff_price
    env.setattr(aa, "lookup_buff_price",
                lambda mh: (buff_calls.append(mh), {"price_twd": 1, "volume": 1})[1])
    post = make_post("紅線 火神 14000 7480")
    aa.process_posts([post])
    assert spy_calls == [], "v2_only 不應呼叫 legacy"
    assert buff_calls == [], "skipped 不應查價"
    assert "_seller_price" not in post


# ================================================================
# 5. seller_price=None 不進數值比較（不 TypeError）
# ================================================================
def test_seller_price_none_never_reaches_numeric_comparison(env):
    env.setenv("ALKAID_V2_PARSER_MODE", "v2_only")
    post = make_post("售 紅線")  # V2 無 SELLER_ASK → seller_price=None
    aa.process_posts([post])  # 不得 TypeError
    assert "_seller_price" not in post


# ================================================================
# 6. V2 結果不再乘 4.5
# ================================================================
def test_v2_result_not_multiplied_by_4_5(env):
    env.setenv("ALKAID_V2_PARSER_MODE", "safe")
    post = make_post("售 AK-47 | 红线 久经沙场 算5000", currency="RMB")
    aa.process_posts([post])
    # V2 已保證 TWD → 不得 ×4.5（5000*4.5=22500）
    assert post["_seller_price"] == 5000, f"_seller_price={post.get('_seller_price')}"


# ================================================================
# 7. legacy 結果保留原 ×4.5 行為
# ================================================================
def test_legacy_result_keeps_current_conversion_behavior(env):
    env.setenv("ALKAID_V2_PARSER_MODE", "off")
    spy_calls = []

    def spy(text):
        spy_calls.append(text)
        return dict(LEGACY_OK)  # seller_price=1000

    env.setattr(aa, "extract_skin_info", spy)
    post = make_post("售 夜行衣 1000", currency="RMB")
    aa.process_posts([post])
    # legacy 路徑保留：1000 × 4.5 = 4500
    assert post["_seller_price"] == 4500, f"_seller_price={post.get('_seller_price')}"
