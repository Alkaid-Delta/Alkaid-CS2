# Alkaid-CS2 V2 Architecture (Phase 0 Snapshot)

> 本文件為 V2 藍圖的專案內快照 + Phase 0 現況紀錄。
> 完整藍圖來源：使用者提供的《Alkaid-CS2 V2 Architecture Blueprint》。
> Phase 0 目標：**建立 regression baseline，不改變任何正式行為。**

## 1. V2 目標（摘錄自藍圖）

1. 防止第一命中誤判商品。
2. 支援一篇貼文多商品、多價格。
3. 每個價格綁定正確商品。
4. 保留原始幣別、只換算一次。
5. 確定性規則與 LLM 推理分離。
6. 簡單任務走 Flash、歧義任務走 Pro。
7. 未驗證商品名稱不得進入市場查價。
8. 每個解析決策可追蹤、可測試。
9. 遷移期間保留舊 `extract_skin_info()` 契約。
10. 先建立 regression dataset 再改正式行為。

## 2. 非目標（Phase 1 不做）

- 不整包重寫 crawler / parser。
- 不一次重構成 package。
- 不移除現有字典。
- 不讓 LLM 算匯率或利潤。
- 不直接 push master。
- 沒有 regression tests 前不改正式行為。

## 3. 目標管線

```
Raw Facebook Post → RawPost Normalizer → Text Parser + Image Parser
→ Candidate Generator → Candidate Scorer → Item/Price Linker
→ Item Validator → Currency Converter → ParsedPost
→ Market Lookup → Deterministic Arbitrage Calculator → LLM Risk Commentary
```

LLM 只做抽取與消歧；**匯率、手續費、利潤、驗證狀態、最終硬過濾**一律 Python 決定。

## 4. 目標 Repository Layout（增量新增，先不動舊檔）

```
alkai_cs2/  (Phase 9 才搬移大函式)
tests/regression/        ← Phase 0 已建立
tests/unit/              ← Phase 1 起
docs/
```

## 5. Phase 0 現況紀錄（2026-08-01）

### 5.1 已建立檔案

| 檔案 | 內容 |
|------|------|
| `tests/regression/fixtures/posts.json` | 10 個手動驗證 golden 案例 |
| `tests/regression/fixtures/expected.json` | 每個案例的期望輸出 |
| `tests/regression/legacy_adapter.py` | 呼叫現有 `extract_skin_info()` 的快照 adapter |
| `tests/regression/test_golden_posts.py` | golden 測試（已知缺陷標 xfail） |
| `tests/regression/report.py` | 產生 baseline report（json + md） |
| `docs/architecture-v2.md` | 本文件 |

### 5.2 未修改的正式行為（Phase 0 承諾）

- `analyze_arbitrage.py` — 行為不變（含 L562 驗證失敗回傳缺陷、字典第一命中缺陷）
- `cdp_fb_crawler.py` — 行為不變（含第一張圖 break）
- 匯率邏輯、模型 Prompt、字典資料 — 不變

### 5.3 已知缺陷（fixture 標記，Phase 1+ 修）

| fixture | 缺陷 |
|---------|------|
| redline_vulcan_simplified | 字典第一命中 return，無法回多商品 |
| redline_vulcan_traditional | 繁中「紅線」不在 pattern 字典 |
| seller_ask_plus_buff_floor | 價格無角色區分（buff_floor vs seller_ask） |
| rmb_price_no_conversion_marker | 字典命中路徑無 currency 欄位 |
| validation_failure_returns_first | 驗證兩次失敗仍回傳第一次名稱（L562） |
| multi_image_second_has_price | crawler 第一張圖成功就 break |

### 5.4 Metrics（Phase 1 起逐項填）

見 `tests/regression/reports/baseline_report.json` 的 `metrics` 區塊。

## 6. 遷移階段

| Phase | 內容 | 狀態 |
|-------|------|------|
| P0 | Regression baseline | ✅ 本分支進行中 |
| P1 | Money + currency service | ⏳ 待批准 |
| P2 | Validation gate | 待 |
| P3 | Candidate collection | 待 |
| P4 | Price extraction/linking | 待 |
| P5 | ParsedPost pipeline | 待 |
| P6 | Multi-image integration | 待 |
| P7 | Flash/Pro router | 待 |
| P8 | Arbitrage boundary cleanup | 待 |
| P9 | Packaging cleanup | 待 |

## 7. 執行規則（Agent 操作守則）

1. 在 `agent/v2-architecture-baseline` 分支工作。
2. 絕不直接修改 master。
3. 每階段前後跑測試。
4. 不把多階段合併成一個 commit。
5. 不靜默改字典資料。
6. 不新增非必要依賴。
7. 不用 LLM 輸出算匯率/利潤。
8. 驗證失敗不得回傳未驗證名稱。
9. 衝突時停止回報。
10. 未經明確批准不 push。
