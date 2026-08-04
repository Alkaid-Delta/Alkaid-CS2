# Phase P0 — Regression Baseline Completion

> HEAD 64bb8a9（P1 seal）之上；production 零修改；readiness = SHADOW_READY

## 1. P0 目標
建立 ≥30 個人工驗證 regression 案例、可重現非空 metrics、明確 pass/known
failure/unresolved 分界，並重新評估 P7 Entry Gate。P0 是 baseline 驗證階段，
**不是功能修正階段**——不修改 production behavior。

## 2. Baseline Case Policy
- 案例唯一 case_id（`[a-z0-9_]{4,64}`）
- 全數 `manual_verified=true`、`source_type=synthetic`（本輪未新增 anonymized_real）
- 45 案例（≥30 達標）覆蓋 A~H 分類 + P7/P8 預置

## 3. Manual Truth Policy
- expected 由人工契約填寫（`truth_author=manual-reviewer-1`、`truth_reviewed`、
  `truth_review_date`、`truth_rationale`）
- 執行 parser 比較差異；known defect 標 xfail；**不修改 expected 配合錯誤 parser**

## 4. Fixture Schema
posts: id/case_id/title/category/source_type/manual_verified/
covered_requirements/expected_status/input/notes/known_defect/truth_*
expected: status/items[{skin,wear,role,seller_price,currency}]/model_used 等
schema 驗證見 `tests/regression/test_fixture_schema.py`（19 項）

## 5. Case Categories（45 case 分布）
A_single_item 5 | B_multi_item 5 | C_price_link 6 | D_currency 6 |
E_validation 8 | F_multi_image 5 | G_legacy_mode 4 | H_failure 4 |
P7_preview 1 | P8_preview 1

## 6. Known Failure Policy
- 固定 case_id + reason_code + severity + affected_phase + remediation_phase
- xfail strict=False（legacy 快照層缺陷）；P7/P8 預置 strict=True（future_gate）
- reason codes: legacy_first_match_return / bare_number_selection_ambiguous /
  knife_tiger_tooth_dict_miss / pattern_without_weapon_unverified /
  multi_image_conflict_unresolved / model_router_not_implemented /
  arbitrage_boundary_not_hardened
- known_failures: 16（報告 p0-known-failures.csv）

## 7. Metrics Definitions
見 report.py compute_metrics：item/seller_price/currency/item_price_link/
verification/image_merge accuracy 為 field-level；分母 0 → null；
P7 metrics → NOT_AVAILABLE_PRE_P7（不填 0 假裝）
currency_accuracy 標 P1-VERIFIED-VIA-INTEGRATION（regression legacy 快照層無
currency 欄位——P1 currency_hardening 61 tests 驗證）

## 8. Determinism Policy
離線 case 5 輪 normalized hash 唯一數 = 1（test_determinism.py）；
排除 timestamp/latency/trace_id/run_id

## 9. Latency Policy
僅離線 legacy pipeline：median 101.57ms / p95 729.19ms（45 case × 5 輪）；
network_calls=0、external_model_calls=0（p0-latency.csv）

## 10. P1/P2 Regression Coverage
P1 currency cases（TWD 不換算/RMB 一次/USD/UNKNOWN fail-closed/計算式 9240）
與 P2 validation cases（trusted dict/alias/retry/forged/validator unavailable）
全數存在且**正式 pass**（不得 xfail）——見 test_p0_coverage.py

## 11. P3/P4 Coverage
multi-candidate/Redline+Vulcan/bundle total/unlinked price/float 非價格等
fixture 覆蓋；legacy 快照層缺陷標 legacy_first_match_return 等 xfail

## 12. P5/P6 Conditions
P5：ParsedPost production 使用由 P1.4 real bridge 測試證明
P6：multi-image 由 P2/P1.4 測試證明（second-image price 為既有 golden skip——
P6 production 缺口仍存在）

## 13. P7/P8 Future Xfail
p0_p7_flash_default_preview（model_router_not_implemented）、
p0_p8_llm_profit_override_preview（arbitrage_boundary_not_hardened）
strict=True；future_gate_xfail_count=2

## 14. Privacy Policy
全 synthetic；無 credential/token/cookie/secret/storage URL/raw image bytes；
test_fixture_schema 檢查（test_no_credential_patterns 等）

## 15. Zero-Network Policy
所有 regression case 離線（extract_legacy 字典/本地路徑）；無真實 API

## 16. Report Reproduction Command
```
python -m pytest tests/regression -q
python "E:/Desktop/Alkaid-CS2-Review-Hub/05-temp-work/P0/generate_p0_reports.py"
```

## 17. P0 PASS Gate（15 項檢查）
① 45 ≥ 30 ✅ ② schema 全合法 ✅ ③ posts/expected sets 相同 ✅
④ P1/P2 cases 正式 pass ✅ ⑤ crash 0 ✅ ⑥ privacy 0 ✅ ⑦ network 0 ✅
⑧ determinism PASS ✅ ⑨ field-level metrics 非空 ✅（currency P1-VERIFIED）
⑩ known failures strict xfail + reason ✅ ⑪ P7/P8 xfail 分離 ✅
⑫ 單一命令重建 ✅ ⑬ full pytest 1758/1/13 無 failed ✅ ⑭ 無新增 skip 掩蓋 ✅
⑮ production 零修改 ✅

## 18. Remaining Gaps
- legacy 快照層：multi-candidate first-match、bare-number 選擇、虎牙字典 miss
- P6：multi_image_second_has_price 仍 golden skip（production 缺口）
- P7/P8 未實作（future xfail）

## 19. P7 Entry Recommendation
見 p7-entry-gate-after-p0.md → **READY FOR P7 WITH CONDITIONS**
（P5/P6 production 整合證據齊備，但 P6 second-image price 為 golden skip；
P7 前置：多圖片價格處理補齊）

## 20. No Production Changes
production-freeze-proof.txt：alkaid_cs2/** 與 analyze_arbitrage.py SHA 前後一致
