# Phase 6.4A–6.4B — Vision Evaluation Dataset & Offline Shadow Evaluation

> 離線評估系統：比較 legacy / text_v2 / vision_raw / vision_production 四 parser 與 ground truth，
> 產出可量化報告與 readiness 建議。**純離線，不影響 production。**

## 1. 本階段目的

用匿名化/synthetic 案例回答「Vision 整合後到底有多準、能不能 safe」，
在切換 production 模式前先量化：名稱、磨損、價格、幣別、linking、conflict、
fallback 比例與 safe gate 的 false positive。

## 2. 為何不能直接切 safe

- 沒有評估資料就切 safe = 把未知風險直接上線
- safe false positive（系統誤放行）會讓錯誤價格進正式輸出
- 本階段 34 案例 readiness 最多 SHADOW_READY；SAFE_PILOT_CANDIDATE 需 ≥50 案例且全部門檻達標

## 3. EvaluationSource

| 值 | 意義 |
|----|------|
| `synthetic` | 人工合成貼文（目前全部） |
| `anonymized_real` | 匿名化真實貼文（後續加入） |
| `manual_fixture` | 手工 fixture |

## 4. EvaluationCase schema

```json
{
  "case_id": "single_twd_safe_001",       // a-z A-Z 0-9 _ -，唯一
  "source": "synthetic",
  "author": "anonymous",                  // 不得真實姓名
  "link": "fixture://case_id",
  "raw_text": "售 AK-47 红线 久经沙场 算5000",
  "images": [EvaluationImage...],
  "expected_items": [ExpectedItem...],
  "expected_post_intent": "selling",
  "expected_safe_for_production": true,
  "tags": ["single", "twd", "safe"],
  "notes": null
}
```

## 5. ExpectedItem 標註規則

- `market_hash_name`（英文官方名）為最優先匹配鍵
- weapon/skin/wear/stattrak 輔助；`seller_price` 是 **seller ask**（非參考價）
- `image_indexes` 記錄該商品出現在哪些圖片
- 多商品貼文必須逐件標註

## 6. EvaluationImage 標註規則

- `image_kind`：inventory/single/multi/market/chat/inspect/payment/trade/other/unknown
- `vision_payload`：**標註用的預期 Vision 回傳**（非真實呼叫）
- `expected_item_indexes`：此圖對應的 expected_items 索引
- `should_create_price`：此圖是否應產生價格證據

## 7. Decimal 使用字串保存

JSON 無 Decimal → `seller_price` 一律字串（`"5000"`）；loader 轉 Decimal，
NaN/Inf 拒絕、≤0 拒絕。

## 8. Ground Truth 建立方式

- synthetic：由人工規則撰寫（目前 34 個）
- 標註重點：正確英文 mhn、正確 seller ask（含幣別）、正確 wear、
  多商品是否 ambiguous、production 最終是否應放行
- conflict 案例的 `expected_safe_for_production=true` 表示「production fallback
  text 後輸出正確」；conflict 偵測另由 conflict_detected 指標評估

## 9. 匿名化資料要求

不得包含：FB cookie / API key / token / 真實私人姓名 / 真實個人 URL /
完整私人聊天內容 / 圖片 base64。

## 10. Legacy / text_v2 / vision_raw / vision_production 比較方式

| parser | 方式 |
|--------|------|
| legacy | `legacy_parser(case.raw_text)`（runner 預設為離線近似，非 DeepSeek 版） |
| text_v2 | `parse_post_for_production(mode="v2_only", vision_inputs=None)` + text items |
| vision_raw | `evaluate_raw_vision_merge()` 的 merged_post（**不經 fallback**） |
| vision_production | `parse_post_for_production(mode="v2_only", 帶 vision)`（fallback 後） |

四組各自獨立計分。`vision_v2` 僅為 vision_production 的向後相容 alias（正式規格不使用）。

## 11. Raw Vision merge 與 production fallback 的差異

- `evaluate_raw_vision_merge()` 回傳 `VisionMergeProductionResult`（merged 原始結果）
- production vision_production 會 fallback（conflict → text_v2 / 全失敗 → skipped）
- **報告同時保留 raw merged（vision_raw）與 production 最終（vision_production）**——否則 fallback 會掩蓋 Vision 本身的錯誤
- vision_production item 來源：fallback→text_items；skipped→空；無 fallback→raw merge merged_post 的 items

## 12. Item exact / partial matching 規則

優先序（deterministic）：
1. mhn case-insensitive 相同 → exact
2. weapon+skin+wear+stattrak 全等 → exact
3. weapon+skin 相同、wear/stattrak 一方缺失 → partial
4. skin-only → 不算匹配
5. 不用價格匹配商品、不依輸入順序硬配
6. 多候選：exact 優先 → partial → stable index

## 13. 簡繁體 alias 僅限 evaluator

`_ALIAS`（戰痕累累↔战痕累累 等）只存在 evaluation comparator，
**不改正式字典**。

## 14. Seller price 比對規則

- exact = Decimal 相同 + Currency 相同 + 同一 matched item
- 5000 TWD vs 5000 RMB 不算 exact（幣別不同）
- seller ask vs BUFF floor/reference 不算 exact
- 禁止換算後比較

## 15. 禁止貨幣換算

evaluation 全程不做任何匯率換算（CurrencyService 不介入）。

## 16. BUFF/REFERENCE 不得視為 seller ask

- market 圖（buff/steam）價格是 reference/buff_floor，不是 seller ask
- `reference_promoted_to_seller` 是獨立錯誤指標（matched item 的 ask 被提升 → 計入 false positive）
- 圖的 seller ask 需賣家語境（evidence 含「售/算」）才成立

### Seller ask 分類（6.4B.4 最終版）
| 情境 | 計數 | 是否計入 FPR |
|------|------|------------|
| GT 有 ask、matched 且正確 | correct_seller_ask | — |
| GT 有 ask、matched 但金額/幣別/無價 | wrong_amount / wrong_currency / missed | — |
| GT 無 ask、matched item 有 ask | **seller_negative_item_false_positives** | ✅ numerator |
| GT 無 ask、額外 unmatched item 有 ask | **extra_unmatched_seller_asks** | ❌ 獨立 |
| GT 有 ask、ask 位於未匹配/錯誤商品 | **seller_ask_on_wrong_item** | ❌ 獨立 |

**`seller_price_false_positive_rate = seller_negative_item_false_positives / seller_price_negative_opportunities`**（0～1；denominator=0 → 0.0 + 保留 denominator 欄位；readiness 要求 denominator>0 且 rate ≤1%）

**統計單位（6.4B.5）**：numerator 以 **Ground Truth negative item** 為單位——同一 negative item 即使掛多筆 seller ask，也只計 1 次 FP（`negative_item_fp_indexes: set[int]` 去重後取 len），因此 `seller_negative_item_false_positives ≤ negative_opportunities` **永遠成立**（不得用 min/clamp 隱藏錯誤）。

三者語意不同：negative-item FPR = matched 負例商品被誤掛 ask（依 item 去重）；extra unmatched asks = 多餘商品上的 ask（GT 無 seller 語境，**可按價格筆數獨立統計**）；wrong-item asks = GT 有 seller 但 ask 錯位（獨立統計）。

> ⚠️ 已廢止（歷史規格，不得使用）：`(false_seller_asks + reference_promoted_to_seller) / negative_opportunities`——此公式把 unmatched 筆數計入 numerator，可能超過 1。

## 17. Safe false positive 定義

**統一 safe 判定（6.4B.4）**：`is_prediction_safe(prediction)` =
`not blocked and parse_status == "parsed" and source != "skipped"`
（unresolved/error 等非 parsed 一律不 safe）；score_case 與 evaluate_safe_decision
共用此函式，不得各自複製條件。

**系統判定可安全放行，但 ground truth 表示不可安全放行**——最重要指標，
比 recall 更重要（誤放行 = 錯誤價格進正式輸出）。

## 18. Safe confusion matrix

| | predicted safe | predicted not safe |
|---|---|---|
| expected safe | TP | FN |
| expected not safe | FP（最危險） | TN |

`safe_false_positive_rate = FP / (FP + TN)`

## 19. Metrics denominator 定義

| metric | denominator |
|--------|-------------|
| item exact rate | expected items 總數（含遺漏） |
| item recall | expected items 總數 |
| seller price exact rate | 有預期 seller ask 的 matched items 數 |
| seller price FP rate | **negative opportunities**（GT 中 seller_price is None 的 expected items 數） |
| currency accuracy | 有 currency 標註的 matched items 數 |
| wear accuracy | 有 wear 標註的 matched items 數 |
| linking accuracy | 有預期 seller price 的 matched items 數 |
| conflict detection | expected conflict 案例數 |
| image type accuracy | 有 image_evidence 的圖片數 |

denominator=0 → 一律輸出 0.0（`_pct` 除零安全）。

## 20. Latency P50/P95 計算方式

- 每 case 每 parser 記錄 `latency_ms`（perf_counter）
- P50 = median、P95 = 排序後 95% 位置
- 只統計 >0 的 latency；空 → 0.0
- **latency 是 runtime metadata**：deterministic 測試排除比較

## 21. Fallback 統計

vision_production 的 `fallback_used`：`none` / `text_v2`（Vision 不安全退文字）/
`skipped`（兩條路都不安全）。報告輸出 fallback_to_text_v2_rate /
fallback_to_skipped_rate。

## 22. Readiness 三種狀態

| 狀態 | 條件 |
|------|------|
| NOT_READY | cases < 25 或 crash > 0 |
| SHADOW_READY | cases ≥ 25、無 crash、P95 可算、safe FP cases 完整列出 |
| SAFE_PILOT_CANDIDATE | cases ≥ 50 + safe FP rate ≤1% + seller price FP ≤1% + currency ≥99% + item exact ≥90% + recall ≥95% + linking ≥95% + malformed crash 0% + all-failed fallback 100% |

## 23. 34 案例最多 SHADOW_READY 的原因

**硬性規則：cases < 50 不得輸出 SAFE_PILOT_CANDIDATE**——案例數不足，
統計無代表性。34 案例即使全過也只到 SHADOW_READY。

## 24. CLI 使用方式

```bash
python scripts/run_vision_evaluation.py \
  --fixtures tests/fixtures/evaluation \
  --output tests/evaluation/reports \
  --format both
```

## 25. CLI 參數

- `--fixtures`：案例目錄（預設 tests/fixtures/evaluation）
- `--output`：報告輸出目錄
- `--limit N`：只跑前 N 個
- `--tag x`：只跑含 tag x 的案例
- `--case-id x`：只跑指定 case
- `--fail-fast`：單案例錯誤立即停止
- `--format json|md|both`

## 26. Exit code

- `0`：全部案例完成且無 runtime crash
- `1`：至少一個案例 runtime crash，或 fail-fast 中止
- `2`：dataset/schema/filter 錯誤

## 27. 如何新增 fixture

1. 複製現有 case JSON 改名
2. 填 raw_text / images[].vision_payload（預期 Vision 回傳）/ expected_items
3. 圖的 payload item 需 `market_hash_name`（英文）才能與 text 對齊
4. seller ask 需 `evidence` 含「售/算」（否則判 UNKNOWN）
5. 跑 runner 驗證無 crash

## 28. 如何加入匿名化真實案例

- 移除姓名/連結/頭像/聊天內容，author="anonymous"
- 圖只保留 `vision_payload`（已提取結果），不放 base64/bytes
- source="anonymized_real"
- 驗證無敏感字串（cookie/token/API key）

## 29. 敏感資料禁止項目

cookie / API key / token / Authorization header / 圖片 bytes / base64 /
完整私人貼文 / 真實私人使用者名稱。

## 30. Deterministic output 規則

- cases 依 case_id 排序（loader）
- parser 順序固定：legacy → text_v2 → vision_raw → vision_production
- safe_false_positive_cases 依 case_id 排序
- tags 依名稱升序；top warning codes 依 (count 降序, code 升序)
- warnings/notes 保留產生順序（去重保序）
- 唯一允許變動：latency（runtime metadata）、git commit hash

## 31. Baseline 報告位置

`tests/evaluation/reports/phase6-4-baseline.json`（機器可讀）
`tests/evaluation/reports/phase6-4-baseline.md`（人讀）
由 runner 實際產生，不得手寫。

## 32. Baseline 目前摘要（自 JSON 讀取，6.4B.5 最終版）

- cases_executed=34、crash_count=0、crash_rate=0.0
- readiness=**SHADOW_READY**
- raw safe 標註：true=6 / false=6 / none=22（正負樣本各 6）
- safe false positive cases（production）：**無**；raw safe matrix：TP=5 FP=1 FN=1 TN=5
- text_v2：item exact 53%、recall 55%、price exact 58%、linking 100%
- vision_raw：item exact 89%、recall 89%、price exact 92%、linking 97%、image type 100%、conflict 100%
- vision_production：與 text_v2 相同（多數案例 fallback text）+ fallback_to_text 29%
- seller price FP rate（6.4B.4）：**全部 0.00%**（denominator=2、negative_item=0）
- extra unmatched asks：全部 0；wrong-item asks 獨立顯示：legacy=28、vision_raw=8
  （GT 有 ask 語境時的錯位 ask，不再誤算為 false positive）
- legacy（離線近似）：TP=22 FP=6（過度放行，參考用）

## 33. 目前主要不足

- **vision_raw 的 seller ask 錯位嚴重**（wrong_item=8；legacy=28）：raw merge 對非 selling 貼文
  與多商品貼文的 ask 常落在未匹配商品——raw safe gate 需 role 感知
- **item exact 53%（production）未達 safe pilot 門檻**（需 90%）
- 多數案例 production fallback text（fallback_to_text 29%）——merged 貢獻尚未穩定進 production
- 繁中字典 miss：傳統中文名（紅線）不命中 pattern_dict（已知限制）
- legacy 離線近似不代表正式 DeepSeek legacy

## 34. 下一階段 P6.4C 建議

- 擴充案例至 ≥50（含 anonymized_real）
- 繁中名稱對齊（evaluation alias → 正式字典擴充需另行評估）
- 多商品 linking 改善（image_order_linking 驗證）
- 真實 legacy（DeepSeek）對照組（需成本控制）
- safe pilot 候選案例集（全部門檻達標後再評估）

## 34.1 Phase 6.4B.1 — Metric Correctness & Raw-Vision Separation

### 四 parser 分流
| parser | 來源 | 用途 |
|--------|------|------|
| legacy | legacy_parser（離線近似） | 舊 baseline 對照 |
| text_v2 | parse_post_for_production(v2_only, 無 vision) | 文字能力 |
| vision_raw | **raw Vision merge 本身**（merged_post） | Vision 真實貢獻 |
| vision_production | production v2_only（含 fallback） | 最終輸出 |

- vision_raw **不得使用 text_items**（指標可與 text_v2 不同）
- raw_merge 只傳給 vision_raw（legacy/text_v2 image_type/conflict = N/A）
- vision_production fallback_to_text 時商品資料明確來自 text ParsedPost

### Prediction 新欄位
stattrak_values / price_types / price_indexes / seller_price_item_indexes /
item_to_price_pairs——每 item 一筆對齊，不只存 selected。

### Currency / Linking 修正
- `get_seller_price_for_item(prediction, item_index)` 統一取價（禁 currencies[item_index]）
- linking：seller price 的 associated item == matched predicted item（經 item_to_price_pairs）

### Seller price 分項
exact / missed / wrong_amount / wrong_currency / false_seller_asks /
reference_promoted_to_seller；report 分開輸出各 rate；
seller_price_false_positive_rate = seller_negative_item_false_positives / denominator
（6.4B.4/6.4B.5 定稿；unmatched 筆數獨立為 extra_unmatched_seller_asks，不入 numerator）。

### Item recall
item_exact_match_rate = exact/expected；item_match_recall = (exact+partial)/expected；
readiness 用 exact ≥90% 與 match_recall ≥95%。

### Raw/Production safe matrix 分開
EvaluationCase 新增 `expected_raw_vision_safe`（bool|None）；conflict fixtures 標 false
（raw 不安全）但 expected_safe_for_production=true（fallback text 後安全）；
None 案例不納入 raw matrix。

### Loader 型別強化
拒絕 str→bool、str→int 靜默轉型；seller_price 只收 JSON string/None；
author/link/raw_text/tags 必須原本 str。

### Report 強化
case-by-case 結果表（不含 payload）；known_limitations 5 項固定寫入；
P95 nearest-rank（ceil(0.95n)-1）；parser 順序 legacy→text_v2→vision_raw→vision_production。

### Phase 6.4B.2 — Production 來源 & Denominator Finalization
> 歷史規格：seller-price FP numerator 定義已由 6.4B.4/6.4B.5 取代（現行 = seller_negative_item_false_positives / negative_opportunities）。

**vision_production item 來源**：
- fallback_to_text → text_items
- source == skipped → 空 items
- Vision 成功未 fallback → raw merge merged_post 的 items（**不得用 text_post**）

**壓縮後 item index**：跳過無 mhn 的 item 後，seller_price_item_indexes /
item_to_price_pairs 用壓縮後 index（source item 1 → compressed 0）。

**Currency 不默認 TWD**：`currencies: list[Currency | None]`；未知/缺失 → None
（`str(Currency.TWD)` 在 3.11 是 "Currency.TWD" 的坑已修：enum 直接保留）。

**Prediction 對齊驗證**：seller_price_item_indexes / currencies / price_indexes /
price_types 長度 == seller_prices；item index 必須 < items 範圍；items 非空時
wear/role/stattrak 必須 0 或同長。

**price_types**：SELLER_ASK 保存時填 "seller_ask"（reference/buff floor 保留原 type）。

**Seller-price FP denominator**：`seller_price_negative_opportunities` =
GT 中 seller_price is None 的 expected items 數；
> 歷史公式已廢止，僅保留版本演進紀錄；現行公式以第 16 節為準
> （seller_negative_item_false_positives / seller_price_negative_opportunities）。
denominator=0 → 0.0 + 輸出 `seller_price_false_positive_denominator` 欄位；
readiness 要求 denominator > 0 且 rate ≤ 1%。

**StatTrak MHH parsing**：`★ StatTrak™ Karambit | Doppler` → (Karambit, Doppler, True)。

**Raw-safe 正負樣本**：6 true（single_twd_safe 等）+ 6 false（conflict/ambiguous）；
報告顯示 raw_safe_expected_true / false / none。

## 35. Rollback / 僅離線評估說明

本階段**零 production 影響**：不切 mode、不改 flag 預設、不呼叫外部 API。
移除 evaluation 套件即可完全回滾（production 無任何依賴）。

## 36. 已知限制

- 34 案例皆 synthetic（無真實貼文變異）
- vision_payload 是標註回傳（非真實 Vision 模型輸出）——模型真實表現需
  6.4C 用真實 analyzer 對照
- legacy 為離線近似
- image type accuracy 在 synthetic 資料下偏高（payload type 與 expected 一致）
- latency 不穩定屬預期（本機執行）
