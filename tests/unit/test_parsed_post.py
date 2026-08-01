"""
test_parsed_post.py — ParsedPost 領域模型測試（Phase 5）

驗證所有欄位約束、mutable default 隔離、輸入獨立性。
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus  # noqa: E402
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType  # noqa: E402
from alkaid_cs2.domain.price import Money  # noqa: E402
from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.services.price_item_linker import LinkDecision  # noqa: E402


def make_item():
    return ItemCandidate(
        market_hash_name="AK-47 | Redline (Field-Tested)",
        skin="Redline", weapon="AK-47", wear="Field-Tested",
        role=ItemRole.SELLING, original_text="售 AK-47 | 红线 久经沙场 5000",
        matched_key="AK-47 | 红线", match_start=2, match_end=12,
        parser="item_parser", evidence=ItemEvidence.DICT_FULL,
        confidence=0.95, score=100.0,
    )


def make_price():
    return PriceCandidate(
        money=Money(5000, Currency.TWD),
        price_type=PriceType.SELLER_ASK,
        source=PriceSource.TEXT,
        evidence="售 AK-47 | 红线 久经沙场 5000",
        confidence=0.9,
    )


def make_decision():
    return LinkDecision(item_index=0, price_index=0, score=100.0, reason="score=100 role=selling type=seller_ask")


def make_post(**overrides) -> ParsedPost:
    base = dict(
        post_id="p1",
        author="測試賣家",
        link="https://fb.com/1",
        raw_text="售 AK-47 | 红线 久经沙场 5000",
        image_urls=["https://img/1.jpg"],
        items=[make_item()],
        prices=[make_price()],
        link_decisions=[make_decision()],
        parse_status=ParseStatus.OK,
        intent=ItemRole.SELLING,
        warnings=[],
        errors=[],
        unlinked_item_indexes=[],
        unlinked_price_indexes=[],
        source="facebook",
        metadata={"lang": "zh-TW"},
    )
    base.update(overrides)
    return ParsedPost(**base)


# ---------------------------------------------------------------
# 1. 正常建立
# ---------------------------------------------------------------
def test_valid_parsed_post():
    p = make_post()
    assert p.post_id == "p1"
    assert p.parse_status is ParseStatus.OK
    assert p.intent is ItemRole.SELLING
    assert len(p.items) == 1
    assert p.source == "facebook"
    assert p.metadata == {"lang": "zh-TW"}


# ---------------------------------------------------------------
# 2. post_id 空白 → raise
# ---------------------------------------------------------------
def test_blank_post_id_raises():
    with pytest.raises(ValueError):
        make_post(post_id="")
    with pytest.raises(ValueError):
        make_post(post_id="   ")


# ---------------------------------------------------------------
# 3. image_urls 型別錯誤
# ---------------------------------------------------------------
def test_invalid_image_urls_type():
    with pytest.raises(TypeError):
        make_post(image_urls=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_post(image_urls="https://img/1.jpg")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 4. image_urls 含空白字串 → raise
# ---------------------------------------------------------------
def test_blank_image_url_raises():
    with pytest.raises(ValueError):
        make_post(image_urls=["https://img/1.jpg", ""])


# ---------------------------------------------------------------
# 5-7. 錯誤型別 → raise
# ---------------------------------------------------------------
def test_wrong_item_type_raises():
    with pytest.raises(TypeError):
        make_post(items=["not-item"])  # type: ignore[arg-type]


def test_wrong_price_type_raises():
    with pytest.raises(TypeError):
        make_post(prices=["not-price"])  # type: ignore[arg-type]


def test_wrong_link_decision_type_raises():
    with pytest.raises(TypeError):
        make_post(link_decisions=["not-decision"])  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 8-9. enum 型別錯誤
# ---------------------------------------------------------------
def test_wrong_status_type_raises():
    with pytest.raises(TypeError):
        make_post(parse_status="ok")  # type: ignore[arg-type]


def test_wrong_intent_type_raises():
    with pytest.raises(TypeError):
        make_post(intent="selling")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 10-11. warnings/errors 含空白 → raise
# ---------------------------------------------------------------
def test_blank_warning_raises():
    with pytest.raises(ValueError):
        make_post(warnings=["no_items", ""])


def test_blank_error_raises():
    with pytest.raises(ValueError):
        make_post(errors=["  "])


# ---------------------------------------------------------------
# 12-14. unlinked indexes 驗證
# ---------------------------------------------------------------
def test_duplicate_unlinked_item_index_raises():
    with pytest.raises(ValueError):
        make_post(unlinked_item_indexes=[0, 0])


def test_out_of_range_unlinked_item_index_raises():
    with pytest.raises(ValueError):
        make_post(unlinked_item_indexes=[5])  # items 只有 1 筆


def test_out_of_range_unlinked_price_index_raises():
    with pytest.raises(ValueError):
        make_post(unlinked_price_indexes=[9])  # prices 只有 1 筆


# ---------------------------------------------------------------
# 15-16. model_used / escalation_reason 空白 → raise
# ---------------------------------------------------------------
def test_blank_model_used_raises():
    with pytest.raises(ValueError):
        make_post(model_used="   ")


def test_blank_escalation_reason_raises():
    with pytest.raises(ValueError):
        make_post(escalation_reason="")


# ---------------------------------------------------------------
# 17. source 空白 → raise
# ---------------------------------------------------------------
def test_blank_source_raises():
    with pytest.raises(ValueError):
        make_post(source="")


# ---------------------------------------------------------------
# 18. mutable defaults 不共享
# ---------------------------------------------------------------
def test_mutable_defaults_not_shared():
    a = make_post(image_urls=["https://a"])
    b = make_post(image_urls=["https://b"])
    assert a.image_urls is not b.image_urls
    a.image_urls.append("https://mutated")
    assert "https://mutated" not in b.image_urls
    # 其他 mutable 欄位
    c = make_post()
    d = make_post()
    assert c.items is not d.items
    assert c.metadata is not d.metadata
    assert c.warnings is not d.warnings


# ---------------------------------------------------------------
# 19. metadata 獨立
# ---------------------------------------------------------------
def test_metadata_copied_or_independent():
    meta = {"lang": "zh-TW", "group": "test"}
    p = make_post(metadata=meta)
    meta["lang"] = "en-US"  # 外部修改
    assert p.metadata["lang"] == "zh-TW", "metadata 應複製而非引用"


# ---------------------------------------------------------------
# 20. image_urls 獨立
# ---------------------------------------------------------------
def test_image_urls_independent():
    urls = ["https://img/1.jpg"]
    p = make_post(image_urls=urls)
    urls.append("https://img/2.jpg")
    assert p.image_urls == ["https://img/1.jpg"]
