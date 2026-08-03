# Phase P1 — Money and Currency Hardening

> HEAD 0409e4e（P2 seal）之上；production mode = off；readiness = SHADOW_READY

## 1. P1 目標

唯一且確定性的貨幣邊界：原始價格/幣別永不覆寫；換算只由
CurrencyService 執行一次；LLM/Vision/OCR/parser 只輸出 original
amount + original currency；TWD 不再乘倍率；所有換算保存
rate_used / rate_source / original。

## 2. 審計發現的 double-conversion 風險（before）

- analyze_arbitrage.py:680 prompt「自動乘 4.5 轉成 TWD」——LLM 換算指示
  （LLM 回傳已換算 TWD 後，Python 端 post.currency=RMB 時再 ×4.5 → 兩次）
- analyze_arbitrage.py:1109 `sp = round(sp * 4.5)`（mode=off 圖片 RMB）
- analyze_arbitrage.py:1156 `sp = round(sp * 4.5)`（legacy/shadow fallback）
- CurrencyService / Money / ConvertedMoney 存在但零使用（審計 8 hits 全在
  domain/定義檔，production 未接入）

## 3-5. Money / ConvertedMoney / CurrencyService contract

- `Money(amount: Decimal, currency: Currency)`——frozen；拒絕 float/bool；
  拒絕負數/NaN/Inf；UNKNOWN 可建立
- `ConvertedMoney(original: Money, twd_amount: Decimal, rate_used: Decimal,
  rate_source: str)`——frozen；rate_used>0；不是 Money（型別防線）
- `CurrencyService(rmb_to_twd, usd_to_rmb, rate_source)`：
  TWD rate=1 / RMB ×rmb / USD ×(usd×rmb) 合併一次 / UNKNOWN raise；
  輸入只接受 Money（ConvertedMoney 拒絕 → 防重複換算）

## 6. Decimal policy

- 核心金額/匯率全 Decimal；`Decimal(float)` 禁止
- 換算結果 `to_integral_value(ROUND_HALF_UP)` 量化為 int（現行顯示規則）
- 原始金額保留輸入精度（seller_price/original_price 原值）

## 7. Single-conversion invariant

唯一換算位置：`_convert_legacy_ask_to_twd(sp, currency, currency_service)`
（analyze_arbitrage.py）——process_posts mode=off 與 legacy/shadow fallback
共用；TWD rate=1 不變；UNKNOWN 不換算（no-conversion）由下游 unresolved
處理。CurrencyService config 一次（rate_source="legacy-static-rate"）。

## 8. Prompt no-conversion policy

initial prompt 移除「自動乘 4.5 轉成 TWD」；JSON schema 改：
`{"market_hash_name", "price": 原始數字, "currency": TWD/RMB/USD/UNKNOWN,
"confidence"}`；嚴禁模型換算/更改幣別。retry prompt 未含換算指示未動。
hash: before cd9aa251… / after 2797780f…

## 9-10. Parser / legacy adapter boundary

- parser 只產生 original Money；PriceCandidate.money 為原始值；
  converted 初始 None（既有 V2 設計維持）
- legacy extract_skin_info：seller_price 欄位語意 = original amount；
  新增 original_price / currency（LLM 路徑）；字典路徑由 process_posts
  `_detect_currency(info, post)` 判定（post.currency 語境）

## 11-12. UNKNOWN / rate_source

- UNKNOWN：不換算（no-conversion）、不進套利、不猜測為 TWD
- rate_source 只允許 manual-config / fixture-rate / operator-approved-rate /
  legacy-static-rate；禁止 unknown/llm/model/vision/ocr

## 13. Error codes（既有 + 沿用）

money_invalid_amount / money_invalid_currency / money_negative_amount /
currency_unknown / currency_service_invalid_rate / currency_already_converted /
currency_rate_source_missing / currency_input_type_invalid /
price_currency_unresolved（固定碼，無動態拼接）

## 14. Double-conversion guards（三層）

1. 型別：CurrencyService 只接受 Money（ConvertedMoney 拒絕）
2. 狀態：PriceCandidate.converted 存在即拒絕再換算（V2 設計）
3. Call-path：唯一 helper `_convert_legacy_ask_to_twd` 只被 process_posts
   兩個換算點呼叫（mode=off / legacy fallback）；無散落 `* 4.5`

## 15. P2 compatibility

P2 全測試保持通過（98）；unverified lookup=0 / forged 拒絕 / mode=off
經 ItemValidator / LLM metadata 透傳 / catalog fail-closed 全維持。
本輪未改 P2 validation semantics 與 VerifiedMarketItem gate。

## 16. P8 freeze

p8-freeze-proof.txt：diff 無 fee/profit/margin/liquidity/minimum_margin/
estimated_profit_twd 修改——只改 seller ask 進入 P8 前的貨幣邊界。

## 17. Test matrix

P1 33 tests（test_money.py 13 + test_currency_hardening.py 20）+ RED 3 →
GREEN；8 組關鍵各 50 輪；P1 20 輪；P1+P2 10 輪；full 3 輪
**1674 passed, 1 skipped, 6 xfailed**。

## 18. Remaining P0 gaps

metrics 未持久化、multi_image golden skip、缺陷編號缺失（P0 修正為
PARTIAL 待後續階段）。

## 19. P7 / B2-B1

尚未開始（P7 entry gate 先前 NOT READY；本輪未動）。

## 20. Phase P1.1 — Currency Fail-Closed Pipeline and Precision Seal

1. **UNKNOWN fail-closed**：resolve_seller_ask_conversion 對 UNKNOWN/
   無幣別回傳 valid=False + price_currency_unresolved——process_posts
   skip（lookup/arbitrage/upload=0），不默認 TWD、不回原值
2. **structured result**：SellerAskConversionResult(original, converted,
   valid, error_code)——不再用 tuple/「-1/no-conversion」偽裝失敗
3. **parse_original_amount**：拒 float/bool/NaN/Inf/科學記號/負數/
   含貨幣字串；Decimal/int/安全字串（保留精度 2100.75）
4. **quantize_twd_for_legacy_display**：僅顯示層 ROUND_HALF_UP；
   CurrencyService 保留完整 Decimal（domain/converted/display 分離）
5. **共用換算 stage**：V2/legacy/shadow/safe/vision 全走
   resolve_seller_ask_conversion；V2 adapter 輸出 currency="TWD"
   （不重複換算）；ConvertedMoney 輸入直接使用不再乘
6. **契約強化**：ConvertedMoney.__post_init__（rate_used>0、rate_source
   安全字元/黑名單 llm/model/vision/ocr/unknown）；CurrencyService
   固定例外（UnsupportedCurrencyError/AlreadyConvertedError/
   MoneyValidationError/InvalidRateConfigurationError）+ exact-type
7. **production call-path 測試**：真實 process_posts + monkeypatch
   計數 lookup/arbitrage/upload/to_twd（UNKNOWN=0/0/0、RMB 一次 9450、
   TWD 不變、float 拒絕、ConvertedMoney 不重複）

## 21. 下一階段人工 gate

人工審核本輪 ZIP → FINAL PASS 後才可 seal commit/push。
