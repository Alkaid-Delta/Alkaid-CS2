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
    GroundTruthReviewStatus,
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
    """讀取單一案例 JSON → EvaluationCase（含 enum/Decimal 轉換、治理欄位、隱私 gate）。"""
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
    case_id = data["case_id"]
    raw_safe = data.get("expected_raw_vision_safe")
    if raw_safe is not None and not isinstance(raw_safe, bool):
        raise TypeError(f"{p.name}.expected_raw_vision_safe 必須 bool 或 None")

    # Phase 6.4C1：治理欄位
    redaction_version = data.get("redaction_version")
    if redaction_version is not None and not isinstance(redaction_version, str):
        raise TypeError(f"{case_id}: redaction_version 必須 str 或 None")
    review_status_raw = data.get("ground_truth_review_status")
    review_status = None
    if review_status_raw is not None:
        if not isinstance(review_status_raw, str):
            raise TypeError(f"{case_id}: ground_truth_review_status 必須 str 或 None")
        try:
            review_status = GroundTruthReviewStatus(review_status_raw)
        except ValueError:
            raise ValueError(
                f"{case_id}: review_status 必須 single_review/double_review/disputed，"
                f"收到 {review_status_raw!r}") from None
    reviewed_by = data.get("ground_truth_reviewed_by")
    if reviewed_by is not None and not isinstance(reviewed_by, str):
        raise TypeError(f"{case_id}: ground_truth_reviewed_by 必須 str 或 None")
    excluded = data.get("excluded_from_readiness", False)
    if not isinstance(excluded, bool):
        raise TypeError(f"{case_id}: excluded_from_readiness 必須 bool")

    case = EvaluationCase(
        case_id=case_id,
        source=source,
        author=_require_str(data.get("author", "anonymous"), f"{case_id}.author"),
        link=_require_str(data.get("link", ""), f"{case_id}.link"),
        raw_text=_require_str(data.get("raw_text", ""), f"{case_id}.raw_text"),
        images=[_parse_image(im, case_id) for im in (data.get("images") or [])],
        expected_items=[_parse_item(it, case_id)
                        for it in (data.get("expected_items") or [])],
        expected_post_intent=_require_str(data.get("expected_post_intent", ""),
                                          f"{case_id}.expected_post_intent"),
        expected_safe_for_production=_require_bool(
            data.get("expected_safe_for_production", False),
            f"{case_id}.expected_safe_for_production"),
        expected_raw_vision_safe=raw_safe,
        tags=[_require_str(t, f"{case_id}.tags") for t in (data.get("tags") or [])],
        notes=data.get("notes"),
        redaction_version=redaction_version,
        ground_truth_reviewed_by=reviewed_by,
        ground_truth_review_status=review_status,
        excluded_from_readiness=excluded,
    )

    # Phase 6.4C1：anonymized_real 必填治理欄位
    if source == EvaluationSource.ANONYMIZED_REAL:
        if not redaction_version:
            raise ValueError(f"{case_id}: anonymized_real 必填 redaction_version")
        if review_status is None:
            raise ValueError(f"{case_id}: anonymized_real 必填 ground_truth_review_status")

    # Phase 6.4C1：隱私掃描 gate（error → 拒絕載入）
    from alkaid_cs2.evaluation.privacy import scan_fixture_for_sensitive_data
    errors = [f for f in scan_fixture_for_sensitive_data(case) if f.severity == "error"]
    if errors:
        codes = ", ".join(f"{f.code}@{f.field}" for f in errors[:5])
        raise ValueError(f"{case_id}: 隱私掃描拒絕載入（{codes}）")
    return case


def load_evaluation_directory(path: str | Path) -> list[EvaluationCase]:
    """讀取目錄全部 .json 案例；重複 case_id → ValueError。"""
    d = Path(path)
    if not d.is_dir():
        raise ValueError(f"目錄不存在：{d}")
    cases: list[EvaluationCase] = []
    seen: dict[str, str] = {}
    for p in sorted(d.glob("*.json")):
        # Phase 6.4C2-A：evaluation_real 目錄的 manifest.json 是治理紀錄
        # （非案例），跳過不載入
        if p.name == "manifest.json":
            continue
        case = load_evaluation_case(p)
        if case.case_id in seen:
            raise ValueError(
                f"重複 case_id={case.case_id!r}（{seen[case.case_id]} 與 {p.name}）")
        seen[case.case_id] = p.name
        cases.append(case)
    return cases
