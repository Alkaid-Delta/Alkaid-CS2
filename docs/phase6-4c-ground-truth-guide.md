# Phase 6.4C1 — Ground Truth 標註指南（Ground Truth Guide）

> 匿名化真實案例（anonymized_real）的 Ground Truth 建立與審核規則。
> 目標：Ground Truth 可複現、可審核、不洩漏隱私、不被 parser 輸出反向污染。

## 1. 文件目的

定義 evaluation dataset 的 Ground Truth 標註標準：商品／價格／幣別／磨損／
safe 標註的判定規則，以及匿名化、雙人審核（double review）、爭議（disputed）
處理流程。**任何不確定之處不得猜測——用 None / unknown。**

## 2. anonymized_real 定義

來自真實 Facebook CS2 社團貼文，經匿名化處理後的案例。

> **6.4C1.1 誠實性修正**：目前 `tests/fixtures/evaluation_real/` 的 10 個案例
> 來源狀態為 **agent_generated（非真實貼文）**，已全部改標 `manual_fixture`
> （`source="manual_fixture"`，notes 註明 agent_generated）。
> **anonymized_real 目前數量 = 0**，直到使用者提供真實案例並完成匿名化與 double review。
- `source="anonymized_real"`
- `author="anonymous"`（強制）
- `link="redacted://<case_id>"`（強制，不得 http/https）
- 必填：`redaction_version`、`ground_truth_review_status`

## 3. adversarial_synthetic 定義

人工設計的對抗案例：模糊、衝突、錯序、重複、低信心、seller ask 錯掛。
`source="adversarial_synthetic"`、`author="synthetic"`。

## 4. synthetic 不得冒充 real

synthetic（含 adversarial）的 Ground Truth 是人工設計的「理想答案」，
不代表真實貼文分布。**不得把 synthetic 標成 anonymized_real**，
反之亦然。報告的 source distribution 必須誠實分開。

## 5. 匿名化流程

1. 保留貼文文字結構（售/收/換、名稱、價格語境）
2. 移除：姓名、帳號、頭像、FB URL、聊天對象、cookie、token、電話、email
3. 圖片：**不 commit 原始 bytes**；只保存 metadata + hash + 標準化 payload
4. 產出 `redaction_version`（如 "1.0"）記錄匿名化版本

## 6. 禁止保存的敏感資料

- 原始圖片 bytes（除非人工建立、完全匿名、無 EXIF、小尺寸、明確標 synthetic）
- http/https URL、facebook.com/fbcdn 網域
- Authorization/Bearer/cookie/api_key/token 內容
- email、台灣手機號碼、長數字 FB ID
- base64 圖片內嵌
- payload 中的 author/user_name/facebook_id/profile_url/sender/recipient 欄位

`scripts/scan_evaluation_privacy.py` 掃描這些項目；error 級發現會**拒絕載入**。

## 7. author / link 規則

- anonymized_real：`author="anonymous"`、`link="redacted://<case_id>"`
- synthetic / adversarial：`author="synthetic"`
- link 只能是 `redacted://` 或 `fixture://` 前綴

## 8. redaction_version 規則

- anonymized_real 必填
- 每次重新匿名化（例如發現洩漏修正後）必須遞增版本
- 格式建議 `MAJOR.MINOR`（如 "1.0" → "1.1"）

## 9. Seller ask 與 Reference 的差異

- **seller ask**：賣家明確開價（「售 X 算5000」「5000 賣」），貼文語境是 selling
- **reference**：市場行情引用（「BUFF 掛牌」「Steam 市價」），貼文語境是詢問/比較
- Ground Truth：seller ask 填 `seller_price`；reference 填 `seller_price=None`
  （圖片 payload 的價格標 `role="reference"`）

## 10. BUFF floor / Steam market 不得視為 seller ask

market 截圖（buff/steam）的價格是 reference/buff_floor，**不是 seller ask**。
貼文文字有「售」語境才可能產生 seller ask。圖片單獨有 market 價格 →
`should_create_price=false`。

## 11. Currency 標註方式

- 有 seller ask → 標 `currency`（TWD/RMB/USD 三選一，與貼文幣別一致）
- 不明確 → `currency=None`（**不得假設 TWD**）
- 幣別衝突案例（圖 RMB vs 文 TWD）→ 保留雙方語境，expected 標主要語境，
  並在 notes 記錄衝突

## 12. Wear 標註方式

- 文字/圖片有磨損 → 標英文 wear（Field-Tested 等）
- 簡繁陷阱：繁「戰痕累累」=Well-Worn、簡「战痕累累」=Battle-Scarred、
  繁「破損不堪」=Battle-Scarred、簡「破损不堪」=Well-Worn
- 無磨損資訊 → `wear` 省略（None）

## 13. StatTrak 標註方式

- 名稱含 StatTrak™ → `stattrak=true`，mhn 帶 `StatTrak™ ` 前綴
- 刀具 StatTrak → `★ StatTrak™ Karambit | ...`
- 無資訊 → `stattrak` 省略（None）

## 14. Knife ★ prefix 標註方式

- 刀具 mhn 帶 `★ ` 前綴（如 `★ Butterfly Knife | Fade (Minimal Wear)`）
- `weapon` 不含 ★（Butterfly Knife）

## 15. Multi-item 標註方式

- 每商品一個 ExpectedItem，各自標 role/price/currency
- 多價格（同商品兩筆 ask）→ 不標單一 seller_price（AMBIGUOUS），
  `expected_safe_for_production=false`

## 16. Multi-image item_indexes 標註方式

- ExpectedItem.image_indexes = 出現該商品的圖片 index 清單
- 同商品跨多圖 → 全列入
- 圖與文對應靠 text 語境 + 圖片順序；不確定 → 只列明確的

## 17. should_create_price 判斷

- 圖片**本身**顯示 seller ask（賣家開價截圖）→ true
- inventory grid（庫存格，無開價）→ false
- market 截圖 → false（reference）
- payment/trade 截圖 → false
- 圖無價格 → false

## 18. Raw Vision Safe 與 Production Safe 差異

- `expected_raw_vision_safe`：**raw Vision merge 本身**是否安全（無 conflict/ambiguous）
- `expected_safe_for_production`：production 最終輸出是否安全
  （conflict → fallback text 後可能安全）
- 例：seller_price_conflict → raw_safe=false、production_safe=true
- 兩者**不得混用**；None = 不納入 raw safe matrix

## 19. Single review 定義

一位 reviewer 標註，未經第二人確認。
- `ground_truth_review_status="single_review"`
- 可載入（error analysis），`excluded_from_readiness=true`

## 20. Double review 定義

兩位 reviewer 獨立標註且結果一致。
- `ground_truth_review_status="double_review"`
- `ground_truth_reviewed_by` 用 reviewer_a/reviewer_b（**不得真實姓名**）
- 唯一可進 readiness 的 real 案例

## 21. Disputed 定義

兩位 reviewer 結果不同、無法協調。
- `ground_truth_review_status="disputed"`
- **不納入 readiness**；可納入 error analysis

## 22. Single / disputed 為何不納入 readiness

readiness 代表「可信案例集的評估結論」；single_review 未確認、
disputed 有分歧 → 不具備可信度。只有 double_review 可信。

## 23. Reviewer 不得參考 parser 結果反向修改 Ground Truth

**禁止**：先看 parser 輸出，再改 Ground Truth 讓分數好看。
Ground Truth 只能來自貼文內容本身。違反此規則的案例必須標 disputed 並丟棄。

## 24. 不確定時使用 None / unknown，不得猜測

- 價格不確定 → seller_price=None
- 幣別不確定 → currency=None
- 磨損看不出 → wear 省略
- 圖片對應不確定 → 不列入 image_indexes
- 猜測會污染評估；None 讓該維度誠實地不計分

## 25. Double-review 衝突處理

1. 兩 reviewer 獨立標註
2. 比對：case_id 外的所有 GT 欄位
3. 一致 → double_review
4. 不一致 → 協調（面對面或第三人仲裁）
5. 協調成功 → double_review（notes 記錄）
6. 協調失敗 → disputed（不得進 readiness）

## 26. Privacy scanner 使用方式

```bash
python scripts/scan_evaluation_privacy.py --fixtures tests/fixtures/evaluation_real
```
- exit 0 = 無 error（可進 dataset）；1 = 有 error；2 = schema/path 錯誤
- 每次新增/修改 anonymized_real fixture 後必須執行

## 27. Analyzer cache 與 fixture payload 的差異

- **fixture payload**：人工標註的「理想 Vision 輸出」（Ground Truth 的一部分）
- **analyzer cache**：真實 Vision Analyzer 對該圖的實際輸出（標準化後）
- `fixture_vs_analyzer` 區塊比較兩者——這是 Vision 品質評估，不是 production 評估
- pytest **永遠只讀 cache**（離線）；cache 的 model/prompt/schema 不符 → miss

## 28. 真實圖片不得直接 commit 的原因

- FB 圖片含 EXIF（GPS/裝置）、可能含人物/頭像/帳號資訊
- repo 是公開的 → 任何 bytes 都可能外洩個人資料
- 只保存 hash + 標準化 payload

## 29. 如何新增匿名案例

1. 取得貼文（crawler 或人工）
2. 匿名化文字與 metadata（§5）
3. 以 fixture 格式寫入 tests/fixtures/evaluation_real/<case_id>.json
4. 填治理欄位（redaction_version、review_status）
5. 跑 privacy scanner（必須 0 error）
6. 標註 GT（§9-§18）
7. 兩位 reviewer 獨立審核（§25）

## 30. 如何重新產生 6.4C1 baseline

```bash
python scripts/run_vision_evaluation.py \
  --fixtures tests/fixtures/evaluation \
  --real-fixtures tests/fixtures/evaluation_real \
  --adversarial-fixtures tests/fixtures/evaluation_adversarial \
  --analyzer-cache tests/fixtures/vision_analyzer_cache \
  --compare-analyzer-cache \
  --output tests/evaluation/reports \
  --report-filename phase6-4c1-baseline \
  --format both
```
不覆蓋 phase6-4-baseline.json（6.4B）。

## 31. 目前 anonymized_real=0（6.4C1.2 誠實性修正）

- `anonymized_real` 目前 **0 個**（無真實使用者提供案例）
- `tests/fixtures/evaluation_real/` 的 10 個案例來源狀態為 agent_generated
  → 已改標 `manual_fixture`（保留 double/single/disputed 治理欄位，
  但**僅驗證治理流程，不代表真實資料**）
- 真實資料門檻（real ≥ 20、double ≥ 15、coverage ≥ 80%）尚未開始累積

## 32. real_data_validation_status=insufficient 的原因

規則（6.4C1.3）：
- anonymized_real == 0 → **insufficient**（現況）
- anonymized_real > 0 且 real cases < 20 / double-reviewed real < 15 /
  **real analyzer coverage < 80%**（任一）→ partial
- 全達標 → complete

manual_fixture 不得計入 real_total、real_double 或 real coverage。
real coverage 只統計 `source=anonymized_real` 的圖片
（`real_analyzer_eligible_images` / `real_cached_analyzer_images` /
`real_external_analyzer_images` / `real_analyzer_coverage_rate`）。

## 33. readiness=SHADOW_READY 的原因

- anonymized_real=0 → 純離線（synthetic + manual + adversarial）→ SHADOW_READY
  （6.4B 語意；無真實資料即無 REAL_DATA_PENDING 的意義）
- known limitations 三態（6.4C1.3）：純 synthetic → all_cases_synthetic；
  synthetic+manual/adversarial → no_anonymized_real_cases +
  all_cases_are_synthetic_or_manual；含 real → REAL_DATA_LIMITATIONS
- cache/comparison/coverage 全部只用 **evaluated_cases**（排除 single/disputed）
- reasons 仍誠實列出：insufficient_eligible_cases（48<50）、
  insufficient_real_case_count、insufficient_double_reviewed_real

## 34. 下一階段前需補足的資料

- **由使用者提供真實案例**（本專案不得自行建立 anonymized_real）
- 匿名化 + privacy scan（0 error）後標 anonymized_real
- double review ≥ 15
- real ≥ 20
- analyzer cache coverage ≥ 80%
- eligible ≥ 50

## 35. 已知限制（6.4C1.4）

- **anonymized_real = 0**、manual_fixture = 10
- mirrored analyzer cache 屬於 manual/synthetic fixture——**不代表真實資料或真實模型準確率**
- 外部執行必須同時具備四條件：`--allow-external-analyzer` flag、
  `EVALUATION_ALLOW_EXTERNAL_ANALYZER=1` env、真實 analyzer adapter、
  真實 image loader（缺一 exit 2）
- fixture_vs_analyzer 比較只統計 evaluated cases（排除 single/disputed）
- real coverage（real_analyzer_coverage_rate）只統計 anonymized_real 圖片
- inclusion flags：single/disputed 預設排除；`--include-single-review` /
  `--include-disputed` 才納入
