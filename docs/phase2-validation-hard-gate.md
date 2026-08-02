# Phase 6.4C2-V2-P2 — Validation Hard Gate

> production mode = off（維持）| anonymized_real = 0 | readiness = SHADOW_READY

## 1. P2 目標

任何準備進入 `lookup_buff_price()` / market lookup / arbitrage analysis /
deal upload 的 item，都必須已通過正式驗證（VerifiedMarketItem）。
未驗證或驗證失敗 → verified=False + 固定錯誤碼 + unresolved/partial，
**不得進入市場查價、套利、上傳**。

## 2. 審計發現的 L594 缺陷

原 `analyze_arbitrage.py`（現行 L594）：LLM 翻譯名稱經 `_verify_skin_on_csgoskins`
驗證兩次失敗後仍 `return data`（回傳第一次未驗證名稱）→ 該名稱可進
`lookup_buff_price`（SQLite LIKE 模糊查）→ arbitrage → upload。
P0 golden fixture `validation_failure_returns_first` 以 xfail 記錄此缺陷。

**修正**：兩次驗證失敗 → 回傳結構化 unresolved 結果
（`market_hash_name=None, verified=False, validation_error=item_validation_retry_failed`）；
`test_validation_failure_returns_first` 由 xfail **轉為正式 pass**（6 passed/6 xfailed）。

## 3. ItemValidator Contract

`alkaid_cs2/services/item_validator.py`：
- `ValidationStatus`（verified / unresolved / invalid）
- `ItemValidationResult`（frozen：original_name / canonical_market_hash_name /
  verified / verified_by / validation_error / attempts / evidence）
- `ItemValidator.validate_candidate(name, *, source)`：受信任 catalog（skin_dict.json）
  驗證；離線、無網路、不查 market price
- `VerifiedMarketItem`（frozen：market_hash_name / verified_by /
  source_candidate_index / validation_digest）——market lookup 唯一合法輸入
- `require_verified_market_item(data)`：production 最後防線（嚴格 bool verified）

## 4. Trusted Validation Sources

`verified_by` allowlist：`trusted_dictionary_exact` / `canonical_catalog` /
`normalized_catalog_alias`。
**禁止單獨視為 verified**：llm / vision / ocr / user_text / fuzzy_match /
legacy_first_result（只能產生 candidate，須再經 catalog 驗證）。

## 5. Retry Policy

`max_attempts = 2`（初次 + 最多一次 retry）；retry 變體 = 移除磨損詞/停用詞後
再查 catalog；兩次失敗 → `item_validation_retry_failed`（attempts=2）；
`max_attempts` 上限強制（>2 拒絕）。

## 6. Fixed Error Codes

`item_validation_empty_name` / `item_validation_invalid_format` /
`item_validation_catalog_miss` / `item_validation_retry_failed` /
`item_validation_conflicting_identity` / `item_validation_service_unavailable`
（frozenset allowlist，不得動態拼字串）。

## 7. VerifiedMarketItem Gate

`require_verified_market_item(data)`：verified 必須嚴格 `is True`
（1 / "true" / None / [] 全拒）+ market_hash_name 非空 + verified_by 在
allowlist → `VerifiedMarketItem`（含 SHA-256 validation_digest）；否則 None。

## 8. Legacy Mode Protection

`process_posts` mode=off 分支（原直接 legacy）：market lookup 前加
`require_verified_market_item` gate——字典命中（verified=True,
trusted_dictionary_exact/normalized_catalog_alias）與 LLM+catalog 驗證成功
（verified=True, canonical_catalog）可進；**unresolved/未驗證 → blocked**。

## 9. Safe/Shadow Fallback Protection

production_bridge safe/shadow 的 legacy fallback 輸出同樣經過 Step 2 gate——
fallback 可取得 candidate，**不可繞過 validation**；V2 blocked/unresolved 時
unsafe legacy candidate → lookup=0（測試驗證）。

## 10. Market Lookup Boundary

Parser 收集 candidates（不宣稱外部驗證）；Validator 做 canonical 驗證；
**lookup_buff_price 只接受 VerifiedMarketItem 來源的 mh**（call site gate）；
不得用 LIKE 模糊結果反向把 candidate 標 verified。

## 11. Fail-Closed Behavior

驗證服務不可用（RuntimeError）→ mode=off 分支 catch → skip + 警告（不 crash、
不洩漏 exception 原文、lookup=0）；未預期 programming error 不用
`except Exception` 吞掉（只 catch RuntimeError）。

## 12. Unresolved Diagnostics

未驗證 item 保留在 ParsedPost.items（不刪除、evidence 保留）；status →
UNRESOLVED/PARTIAL；market_hash_name=None；validation_error 固定碼。

## 13. Test Matrix

- unit（19）：exact/alias/unknown/empty/retry 成功/retry 失敗/attempts 上限/
  LLM+Vision 不單獨 verified/型別拒絕/allowlist/一致性/deterministic/無網路
- integration（23）：gate 嚴格 bool/legacy L594/mode=off/shadow/safe/v2_only/
  vision/retry exhausted/validator unavailable/verified 唯一進 lookup 一次/
  zero secret env/zero network/P2 fixtures 存在性
- regression：`test_validation_failure_returns_first` xfail → **pass**
  （6 passed, 1 skipped, 6 xfailed）；fixtures 13 → **21**

## 14. Zero-Network Proof

socket.socket / create_connection / getaddrinfo / urllib.urlopen /
HTTPConnection 全封鎖下執行 validator + gate 全流程 → network call count = 0
（test_no_network_in_validator / test_gate_zero_network）。

## 15. 尚未處理的 P1/P8 問題（本輪 scope 外）

- P1：LLM prompt 仍指示 RMB×4.5（analyze_arbitrage.py:549）；CurrencyService
  未接線；csgoskins_bridge/fetch_prices float 匯率——**未動**
- P8：LLM prompt 仍含 estimated_profit_twd 且採用 LLM 值（:741/:760）；
  deterministic hard filters 未實作——**未動**

## 16. Trusted Dictionary Candidate 標記（續修正）

- **full dictionary exact match**（item_parser.py L331）：`verified=True`,
  `verified_by="trusted_dictionary_exact"`, `validation_error=None`
- **pattern/normalized trusted alias**（item_parser.py L353）：`verified=True`,
  `verified_by="normalized_catalog_alias"`（pattern 字典為受信任 canonical
  alias 映射，非規則推測）
- **LLM / Vision / OCR / fuzzy / unknown**：一律 `verified=False` 保持
  unverified（vision_adapter.py:464 已是）
- **legacy_adapter.py 透傳**：`legacy_data` 輸出含
  `verified`（嚴格 bool）/ `verified_by` / `validation_error`（不自行補 True、
  不原地修改 caller candidate）
- **3 個 regression 失敗根因**：item_parser 的 ItemCandidate 未設 verified →
  P2 hard gate 將正常字典結果誤判 unverified → `post["_seller_price"]` KeyError；
  修正後全轉 pass（50 輪穩定）
- **hard gate 未放寬**：`require_verified_market_item` 維持嚴格
  `verified is True` + verified_by allowlist（fail-closed 不變）

## 17. Phase P2.1 — Canonical Validation Tightening

1. **信任層級**：full exact key → trusted_dictionary_exact；full normalized
   full equality / explicit alias → normalized_catalog_alias；canonical English
   market name exact → canonical_catalog；**pattern 命中預設 unverified**
   （pattern without weapon 不得進 market lookup）
2. **substring validation 已移除**：ItemValidator._lookup 不再用
   `cn in name`；改用 exact / normalized full equality / canonical English
   集合（full_cn_to_en values）比對；item_parser full 命中加詞邊界檢查
   （「半件AK-47 | 红线複製品」不再 exact 命中）
3. **skin-only 防線**：「Redline」「Vulcan」等不得進 gate（require_verified_
   market_item 對 mhn 執行 canonical catalog 再驗證）
4. **forged dict 防線**：verified=True + verified_by=trusted + mhn="Redline"
   這類偽造 dict 被 gate 拒絕（canonical 再驗證）；只有 ItemValidator 的
   catalog 集合內名稱可建立 VerifiedMarketItem
5. **pattern + weapon**：組裝出的完整名稱經 ItemValidator.validate_market_name
   （canonical English 集合）通過才 verified（canonical_catalog）；
   catalog 不可用 → fail-closed unverified
6. **B2/production guard（方案 B）**：test_production_files_unchanged 改為
   phase-aware allowlist——工作樹變更僅限 P2 核准檔案；B2 evaluation/ 與
   其他 production 檔被修改即失敗；新階段加入時人工更新 allowlist
7. **既有路徑保護**：3 個原 regression 失敗測試仍過（fixture 用 full dict /
   canonical 確認名稱，非靠 pattern 全標 verified）
8. **P1/P8 仍未處理**（currency conversion / profit prompt 未動）

## 18. Phase P2.2 — Legacy Validation Convergence

1. **統一 ItemValidator**：legacy full/pattern/LLM 路徑全部經同一
   ItemValidator canonical 驗證（_validate_legacy_candidate helper）；
   verified metadata 唯一來源 = ItemValidator（legacy 不再自行宣告
   trusted_dictionary_exact / normalized_catalog_alias）
2. **dictionary match 只是 candidate evidence**：組裝名稱後呼叫
   validate_market_name（canonical English 集合）；full substring 命中
   不得直接宣稱 trusted exact；pattern 命中不得 normalized alias
3. **mode=off pattern 防線**：無武器組裝名（skin-only）→ unresolved →
   lookup=0/arbitrage=0/upload=0（golden fixture legacy_single_nocts
   expected 更新為 unresolved——P2.2 收斂的正確期望，非掩蓋）
4. **broad exception 移除**：dictionary block 只 catch OSError/
   JSONDecodeError → raise RuntimeError(catalog_unavailable)；catalog
   檔案不存在亦 fail-closed（不得落入 LLM）；AST 測試驗證
5. **catalog unavailable**：RuntimeError → process_posts fail-closed
   （LLM client call count=0、lookup=0）
6. **external verification 不能取代 local catalog**：csgoskins 成功後
   仍須本地 ItemValidator 接受才 verified（LLM 初次與 retry 皆然）
7. **P1/P8 仍未處理**（LLM prompt RMB 指示、profit prompt 未動）

## 19. Phase P2.3 — Structured Validation Result and Evidence Repair

1. **verified metadata 來自 ItemValidationResult**：_validate_legacy_candidate
   呼叫 validate_candidate(name, source=source)，回傳欄位
   （market_hash_name/verified/verified_by/validation_error/attempts/
   original_name）完全來自 result；helper 只做結構轉換，不自行宣告
   任何 verified 值（AST + 透傳測試驗證）
2. **source 傳遞**：legacy_dict_full / legacy_dict_pattern / legacy_llm /
   legacy_llm_retry 全部實際傳入 validate_candidate（monkeypatch 測試）
3. **canonical wear preservation**：_decompose 拆 identity/prefix(StatTrak™)/
   wear；canonical 組裝保留合法 wear 與 ★/StatTrak™ 前綴
   （"AK-47 | Redline (Field-Tested)" → 同；★ 為 catalog 資料一部分，
   集合同時註冊無 ★ alias；"★ StatTrak™" 疊加統一格式）
4. **retry exception boundary**：try 只包 API + JSON parse
   （except JSONDecodeError/TypeError/AttributeError → data2=None →
   unresolved retry_failed）；本地 validation 移出 try——programming
   errors 不得吞；外層 containment except Exception 前先
   except (TypeError, AttributeError, NameError): raise
5. **authentic RED evidence**：git worktree add --detach f311625 →
   E:\Desktop\Alkaid-CS2-P2-Red-Proof-Temp → 只放 RED 測試 →
   真實執行 6 failed → worktree remove --force → worktree list 驗證；
   reconstructed 檔標記 RECONSTRUCTED — NON-AUTHORITATIVE
6. **P1/P8 仍未處理**（LLM prompt RMB 指示、profit prompt 未動）

## 20. Phase P2.4 — LLM Validation Metadata Convergence and Exception Boundary Seal

1. **初次/retry 完整透傳 ItemValidationResult**：LLM 成功路徑不再重建
   verified/verified_by/validation_error——`_apply_validation_result` 只從
   result 寫入 market_hash_name/verified/verified_by/validation_error/
   validation_attempts（canonical name 取自 validator；不硬編碼
   canonical_catalog；unverified 不升級；不原地改 payload）
2. **`_call_legacy_llm_json` transport helper**：只包單一 API 呼叫 +
   JSON parse；捕捉具體例外——JSONDecodeError/TypeError/AttributeError
   （parse）+ openai SDK 4 型（APIError/APIConnectionError/
   APITimeoutError/RateLimitError，ImportError 時空 tuple）；無 broad except
3. **本地 validation 在 transport try 外**：_verify_skin_on_csgoskins /
   _validate_legacy_candidate / _apply_validation_result 全在 helper try 外——
   RuntimeError（catalog unavailable）向上傳播 fail-closed；ValueError/
   TypeError/AttributeError 不得被吞（測試證明）
4. **extract_skin_info 外層 broad except 移除**：LLM 區塊不再有
   except Exception 包住 validation（保留 (TypeError, AttributeError,
   NameError): raise）
5. **初次 transport/parse 失敗** → 安全 unresolved
   （item_validation_service_unavailable）；retry 失敗 →
   item_validation_retry_failed——lookup=0
6. **corrected authentic RED**：worktree（detached f311625）PROJECT_ROOT
   修正指向 repo root——5 failed 全為 assertion failure 直接對應缺陷，
   無 FileNotFoundError/ImportError/harness error
7. **P1/P8 仍未處理**（prompt 文字 byte-for-byte 未變——hash proof）

## 21. 下一階段人工 Gate

P2 完成待審核（ZIP → Review Hub 01-current）。審核通過後：
P1 currency conversion（移除 LLM 換算 + CurrencyService 接線）→ P8 arbitrage
boundary → P7 entry gate 重評。
