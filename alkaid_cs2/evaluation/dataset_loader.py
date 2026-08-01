"""
dataset_loader.py — Evaluation 案例載入（Phase 6.4A）

- 僅讀取 .json、穩定檔名排序
- JSON error 提供檔名與原因
- 重複 case_id → ValueError
- 壞案例不靜默跳過
- 不得修改原始 JSON、不執行 payload
"""
import json
from pathlib import Path

from alkaid_cs2.evaluation.models import (
    EvaluationCase,
    EvaluationImage,
    EvaluationSource,
    ExpectedImageKind,
    ExpectedItem,
    parse_currency,
    parse_decimal,
)


def _require_bool(value: object, name: str) -> bool:
    """必須原本就是 bool（拒絕字串/數字靜默轉型）。"""
    if not isinstance(value, bool):
        raise TypeError(f"{name} 必須原本就是 bool，收到 {type(value).__name__}")
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必須原本就是 str，收到 {type(value).__name__}")
    return value


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必須原本就是 int（拒絕 bool），收到 {type(value).__name__}")
    return value


def _parse_item(raw: dict, case_id: str) -> ExpectedItem:
    sp_raw = raw.get("seller_price")
    # seller_price 只接受 JSON string 或 None（不接受 float）
    if sp_raw is not None and not isinstance(sp_raw, str):
        raise TypeError(f"{case_id}.seller_price 只接受字串或 None，收到 {type(sp_raw).__name__}")
    seller_price = parse_decimal(sp_raw, f"{case_id}.seller_price")
    return ExpectedItem(
        market_hash_name=raw.get("market_hash_name"),
        weapon=raw.get("weapon"),
        skin=raw.get("skin"),
        wear=raw.get("wear"),
        stattrak=raw.get("stattrak"),
        role=raw.get("role"),
        seller_price=seller_price,
        currency=parse_currency(raw.get("currency")),
        image_indexes=raw.get("image_indexes") or [],
        notes=raw.get("notes"),
    )


def _parse_image(raw: dict, case_id: str) -> EvaluationImage:
    kind_raw = raw.get("image_kind")
    if not isinstance(kind_raw, str):
        raise TypeError(f"{case_id}.image_kind 必須字串，收到 {type(kind_raw).__name__}")
    try:
        kind = ExpectedImageKind(kind_raw)
    except ValueError:
        raise ValueError(f"{case_id}: 未知 image_kind={kind_raw!r}") from None
    return EvaluationImage(
        image_index=_require_int(raw.get("image_index"), f"{case_id}.image_index"),
        image_url=_require_str(raw.get("image_url"), f"{case_id}.image_url"),
        image_kind=kind,
        vision_payload=raw.get("vision_payload"),
        expected_item_indexes=raw.get("expected_item_indexes") or [],
        should_create_price=_require_bool(raw.get("should_create_price", False),
                                          f"{case_id}.should_create_price"),
        notes=raw.get("notes"),
    )


def load_evaluation_case(path: str | Path) -> EvaluationCase:
    """讀取單一案例 JSON → EvaluationCase（含 enum/Decimal 轉換）。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p.name}: JSON 解析錯誤：{exc}") from None
    except OSError as exc:
        raise ValueError(f"{p.name}: 讀取失敗：{exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{p.name}: 頂層必須是物件")
    if "case_id" not in data:
        raise ValueError(f"{p.name}: 缺少 case_id")
    if not isinstance(data["case_id"], str):
        raise TypeError(f"{p.name}.case_id 必須 str")
    source_raw = data.get("source")
    if not isinstance(source_raw, str):
        raise TypeError(f"{p.name}.source 必須字串")
    try:
        source = EvaluationSource(source_raw.lower())
    except ValueError:
        raise ValueError(f"{p.name}: 未知 source={source_raw!r}") from None
    raw_safe = data.get("expected_raw_vision_safe")
    if raw_safe is not None and not isinstance(raw_safe, bool):
        raise TypeError(f"{p.name}.expected_raw_vision_safe 必須 bool 或 None")
    case = EvaluationCase(
        case_id=data["case_id"],
        source=source,
        author=_require_str(data.get("author", "anonymous"), f"{p.name}.author"),
        link=_require_str(data.get("link", ""), f"{p.name}.link"),
        raw_text=_require_str(data.get("raw_text", ""), f"{p.name}.raw_text"),
        images=[_parse_image(im, data["case_id"]) for im in (data.get("images") or [])],
        expected_items=[_parse_item(it, data["case_id"])
                        for it in (data.get("expected_items") or [])],
        expected_post_intent=_require_str(data.get("expected_post_intent", ""),
                                          f"{p.name}.expected_post_intent"),
        expected_safe_for_production=_require_bool(
            data.get("expected_safe_for_production", False),
            f"{p.name}.expected_safe_for_production"),
        expected_raw_vision_safe=raw_safe,
        tags=[_require_str(t, f"{p.name}.tags") for t in (data.get("tags") or [])],
        notes=data.get("notes"),
    )
    return case


def load_evaluation_directory(path: str | Path) -> list[EvaluationCase]:
    """讀取目錄全部 .json 案例；重複 case_id → ValueError。"""
    d = Path(path)
    if not d.is_dir():
        raise ValueError(f"目錄不存在：{d}")
    cases: list[EvaluationCase] = []
    seen: dict[str, str] = {}
    for p in sorted(d.glob("*.json")):
        case = load_evaluation_case(p)
        if case.case_id in seen:
            raise ValueError(
                f"重複 case_id={case.case_id!r}（{seen[case.case_id]} 與 {p.name}）")
        seen[case.case_id] = p.name
        cases.append(case)
    return cases
