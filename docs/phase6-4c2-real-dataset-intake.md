# Phase 6.4C2-A — Real-World Dataset Intake, Anonymization and Double-Review Workflow

> **目前沒有 anonymized_real。**
> 本階段只建立 intake 與 review workflow——**不代表已完成 real-world validation**。

## 1. 目的

建立由使用者安全提供真實案例的完整流程：原始資料（Git 外）→ 匿名化 →
intake manifest → Reviewer A/B 獨立標註 → review diff → double_review /
disputed →（必要時）adjudication → 進 evaluation fixtures（anonymized_real）。

## 2. anonymized_real 的合法來源

只有以下 provenance 可標 `anonymized_real`：

- `user_supplied_real`（使用者直接提供）
- `user_authorized_collection`（使用者授權收集）
- `internal_owned_source`（內部自有來源）

## 3. Prohibited Provenance

**不得**使用：`agent_generated`、`synthetic`、`inferred_real`、`manual_fixture`、
`adversarial_synthetic`。CLI 以 argparse choices 直接拒絕（exit 2）。
`can_mark_anonymized_real()` 是第二道 gate。

## 4. Authorization 規則

`consent_or_authorization` allowlist：

- `user_supplied` / `owner_authorized` / `internal_owned`

未知、空白、含糊值（例如 maybe）一律拒絕。

## 5. Git 外圖片保存

真實圖片**不得**進入：tests/fixtures、repository、Git LFS、cache fixture、
report、Markdown。一律存 Git 外 secure storage，以
`secure-store://<opaque-id>` 參照（opaque id 限 `[a-z0-9_-]`）。

Evaluation fixture 只可保存：image_index、opaque reference、image SHA-256、
redacted SHA-256、image metadata、Ground Truth payload、analyzer cache key。
**不得**保存：image bytes、base64、data URL、原始 FB URL、EXIF、thumbnail bytes。

## 6. Intake Manifest

- `RealCaseIntakeManifest.__post_init__` 直接驗證 image hash schema
  （64 位小寫 hex、無重複、數量與 image_count 一致）——直接建構 model 也不得繞過
- original/redacted_image_hashes 由 CLI 保存（不驗證後丟棄）
- image_count 非 int/bool/負數 → 受控 validation failure、CLI exit 2（不 traceback）
- hash 欄位（fixture_sha256/original_image_hashes/redacted_image_hashes/
  reviewer_inputs_hash/final_ground_truth_hash）由專用 SHA-256 validator 驗證，
  **不被 generic base64 heuristic 誤判**

`RealCaseIntakeManifest`（`alkaid_cs2/evaluation/intake_models.py`）欄位：
intake_id / case_id / source_type / source_provenance / consent_or_authorization /
original_storage_reference / redaction_version / collected_at / collection_method /
redaction_status / redacted_by / privacy_scan_status / reviewer_a_status /
reviewer_b_status / adjudication_status / final_review_status / image_count /
original_image_hashes / redacted_image_hashes / notes。

不保存 raw_text 全文、payload、image bytes。

## 7. 匿名化流程

`scripts/redact_real_case.py`（`redaction.py`）：

- deterministic、不呼叫 LLM、不連網、不自動補商品/價格/幣別/Ground Truth
- **draft 不得保留任何預載 Ground Truth**（expected_items/post_intent/safe flags/
  seller_price/currency/wear/stattrak/role/should_create_price/item_image_indexes/
  image_kind）——全部由 reviewer workflow 建立（Phase 6.4C2-A.2/A.3）
- 移除/替換：真實姓名、sender/recipient、author id、profile URL、FB/fbcdn URL、
  email、台灣手機、token、cookie、Authorization、api_key、base64、data URL、
  image bytes、EXIF、長數字 ID、本機絕對路徑
- 匿名化後：author="anonymous"、link="redacted://<case_id>"、
  ground_truth_review_status 預設 single_review
- 全部 gate 通過 → `source=anonymized_real`；否則 `unverified_real_draft`

## 8. Privacy Scanner

`scan_evaluation_privacy.py` 對 fixtures 掃描；`intake_validation.scan_redaction_issues`
對 draft 遞迴掃描（含 nested dict/list、bytes 值）。0 error 才可進 fixtures。

## 9. Reviewer A/B 獨立標註

- `review_real_case.py --dry-run` 與正式模式執行**相同驗證**
  （reviewer allowlist、case_id、validate_annotations、nested privacy），
  只跳過 reviewer JSON 寫入（Phase 6.4C2-A.4）
- 所有 CLI schema/JSON/檔案錯誤 → **exit 2、不 traceback**
- `create_real_case_intake.py` 的 image_count 必須**非 bool 的 int**
  （不接受 "1"/1.0/true/false/負數，不自動轉型）

`scripts/review_real_case.py` 寫 `reviewer_a.json` / `reviewer_b.json`——
檔名由 reviewer_id 決定，結構上無法覆寫對方（獨立性保證）。
Reviewer identity 限 reviewer_a / reviewer_b / reviewer_c，**不得真實姓名**。

## 10. Review Diff

`scripts/compare_real_case_reviews.py` 比較欄位（11 項）：
expected_items / seller_price / currency / wear / stattrak / item_image_indexes /
expected_raw_vision_safe / expected_safe_for_production / image_kind /
should_create_price / role。結果：exact_match / semantic_match / mismatch /
missing_on_a / missing_on_b。

## 11. Adjudication

`scripts/adjudicate_real_case.py`：

- 只有 disputed 可進；由 reviewer_c / adjudicator 決議
- **`--final-gt-json` 必填**（Phase 6.4C2-A.2/A.3）：final Ground Truth 不得
  None/空 dict/不過 ReviewAnnotation schema
- CLI `--case-id` 必須與 review case_id 相同
- 保存兩檔（Phase 6.4C2-A.6 **atomic commit unit**）：
  - `adjudication.json`：metadata + reviewer_inputs_hash + final_ground_truth_hash
  - `final_ground_truth.json`：canonical 最終 GT（sorted keys + compact，可重建）
- **adjudication.json 與 final_ground_truth.json 是同一 atomic commit unit**：
  任一寫入／replace 失敗 → 不得留下部分新狀態、既有正式檔保留、
  temp 清理、reviewer A/B 不受影響（atomic_write_pair + rollback）
- **rollback 完整成功才清除 backup**；rollback 不完整時
  `.bak.<nonce>` 是唯一 recovery copy，**不得自動刪除**（供人工復原）
- rollback failure 不代表兩個正式檔一定存在；保留 backup 是唯一可恢復來源
- commit/rollback 狀態：commit_complete / rollback_complete / preserve_backups
- **reviewer_a.json、reviewer_b.json、adjudication.json、final_ground_truth.json
  共同構成可重建且可稽核的 Ground Truth source of truth**（hash 可交叉驗證）
  - reviewer_a.json / reviewer_b.json：兩位 reviewer 的獨立原始標註
  - adjudication.json：adjudicator、reason、timestamp、reviewer_inputs_hash、
    final_ground_truth_hash
  - final_ground_truth.json：canonical 最終 Ground Truth
  - **compare 輸出只是衍生 decision，不是 Ground Truth source of truth**
- **dry-run 做完整驗證（disputed/adjudicator/reason/case_id/final GT schema/hashes），
  只跳過寫檔**（write_files=False）
- **不得刪除原 reviewer A/B 標註**
- **不得參考 parser 預測或 analyzer 輸出作決策**（API 拒絕傳入）

## 12. Source of Truth

reviewer_a.json、reviewer_b.json、adjudication.json、final_ground_truth.json
**共同構成可重建且可稽核的 Ground Truth source of truth**（與第 11 節一致）：

- reviewer_a.json / reviewer_b.json：兩位 reviewer 的獨立原始標註
- adjudication.json：adjudicator、reason、timestamp、reviewer_inputs_hash、
  final_ground_truth_hash
- final_ground_truth.json：canonical 最終 Ground Truth

compare 輸出只是一個衍生 decision，**不是** Ground Truth source of truth。

補充（Phase 6.4C2-A.5）：

- 不確定價格／幣別／wear 可使用 **None**（schema 不得逼 reviewer 猜測）
- CLI 的 `--notes` 也受 privacy scanner 保護（error → exit 2、不回顯、不寫檔）
- 所有 CLI validation／read／write error 都應 exit 2、不 traceback，且**只輸出固定 error code**：
  - intake：invalid_provenance / invalid_authorization / input_not_found /
    input_invalid_json / input_read_failed / image_count_invalid_type /
    image_count_negative / image_hash_validation_failed / storage_reference_invalid /
    manifest_validation_failed / manifest_write_failed
  - adjudicate：review_validation_failed / review_read_failed /
    final_gt_invalid_json / final_gt_read_failed / case_id_mismatch /
    not_disputed / validation_failed / atomic_write_failed
  - **不回顯** exception 原文、path、case ID、reviewer ID、provenance/
    authorization 原值、image_count repr、hash 原值、JSON fragment、payload、
    str(exc)、repr(value)
- dry-run：完整驗證、不建立正式檔、不建立 temp、不建立 backup

## 13. Synthetic/Manual 不得計入 Real

readiness 門檻只數 `source=anonymized_real`：real ≥ 20、double ≥ 15、
real analyzer coverage ≥ 80%。manual_fixture / synthetic / adversarial
一律不計入 real thresholds。

## 14. 目前 real=0

`anonymized_real=0`、`manual_fixture=10`、`real_data_validation_status=insufficient`、
`readiness=SHADOW_READY`。intake framework 完成**不**提升 readiness。

## 15. 如何由使用者提供案例

1. 使用者提供本機原始 JSON（含 raw_text、圖片 metadata、seller/reference 資訊）
2. `scripts/redact_real_case.py --input <raw> --case-id <id> --source-provenance
   user_supplied_real --authorization user_supplied --redaction-version v1`
3. 檢視 draft（local_data/real_intake/redacted/）→ `scan_evaluation_privacy.py`
4. `scripts/create_real_case_intake.py` 建立 manifest
5. Reviewer A/B 各自 `scripts/review_real_case.py` 標註
6. `scripts/compare_real_case_reviews.py` → double_review / disputed
7. disputed → `scripts/adjudicate_real_case.py`
8. 通過的 case 以 anonymized_real 進 `tests/fixtures/evaluation_real/`

## 16. 不得上傳原始私人資料到公開 repository

原始 JSON、圖片 bytes、真實姓名/帳號/URL/電話/email/token/cookie 一律留在
Git 外（local_data/ 有 .gitignore 保護；secure storage 自行管理）。

## 17. 下一階段 Analyzer 執行前置條件

真實 analyzer 執行（P6.4C2-B）需同時具備：`--allow-external-analyzer` flag、
`EVALUATION_ALLOW_EXTERNAL_ANALYZER=1` env、真實 analyzer adapter、
真實 image loader（從 secure storage 讀 bytes、不寫回 repository）。

## 18. Rollback / Delete

- draft 可安全刪除（local_data/real_intake/redacted/）
- manifest 刪除 = 該案例不進 readiness（無副作用）
- 已進 fixtures 的 anonymized_real：移除 fixture + manifest 更新
  （manifest 是治理紀錄，保留歷史）
- reviewer 標註誤寫：重新以正確 reviewer 寫入（不覆寫他人檔案）
- 原始資料刪除由使用者自行管理（Git 外）
