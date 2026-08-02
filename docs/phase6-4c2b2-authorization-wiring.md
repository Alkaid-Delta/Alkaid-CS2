# Phase 6.4C2-B2-B0 — Authorization Wiring and Audit v3 Integration

> **production mode = off** | **anonymized_real = 0** | **external analyzer executed = no**
> **real image bytes loaded = no** | **readiness = SHADOW_READY**

## 1. 階段目標

把 B2-A 封版的 AuthorizationContextV1 / AuthorizationDecision / NetworkPolicyV1 /
Audit v3 接入 external analyzer execution preflight——授權在任何 byte load、
cache access、adapter call 之前完成；blocked 時零呼叫、寫 Audit v3 blocked。

## 2. B2-A 與 B2-B0 邊界

- B2-A：契約層（context/decision/policy/v3 schema）——已封版
- B2-B0：接線層（wrapper + preflight 整合 + v3 寫入）——本次
- 未接：real adapter、HTTP client、cloud loader、execute-real CLI、network allowlist

## 3. 為何不再信任 plan.authorized

`plan.authorized` 保留為 B1 legacy informational field（不刪除，避免破壞 B1
schema），但**不再作為授權依據**。執行授權完全由
`evaluate_execution_authorization(...)` 回傳的 `AuthorizationDecision.authorized`
決定——即使 `plan.authorized=True`，只要 decision=False → blocked（loader/
adapter/cache/bytes 全零）。

## 4. Authorized Wrapper API

```python
execute_authorized_external_analyzer_plan(
    *, plan, eligible_cases, loader, adapter, cache_dir, audit_dir,
    allowed_root, analyzer_name, analyzer_version, authorization_input)
```

策略 A：**B1 `execute_external_analyzer_plan` 完全不動**（legacy fake engine，
寫 Audit v2）；新增 wrapper 只供 B2 路徑使用。wrapper 流程：
started_at → 授權 decision → blocked（v3 blocked audit，不呼叫 B1 engine）
→ authorized（委派 B1 engine，最終寫 v3 audit）。

## 5. Authorization Input Schema

`AuthorizationExecutionInputV1`（frozen dataclass）：flag/env/context/network_policy
+ 11 項 expected bindings + 5 項 requested budgets + now_utc。不含 secret env value/
API key/token/cookie/endpoint/image bytes/case ID/storage reference；不從 global env
自動讀取 credentials。

## 6. Binding 規則

repository/branch/commit_sha/manifest_sha256/**approved_run_id（= 實際 plan.run_id）**/
loader name+version/adapter name+version/config_sha256/network_policy_version 全數由
caller 提供 expected 值（helper 內不執行 shell `git rev-parse`）。任一 mismatch →
blocked + 固定碼。

## 7. Requested Budget 規則

`requested_case_count == len(eligible_cases)`、`requested_image_count == 總 image 數`
（不一致 → `authorization_requested_case_count_mismatch` /
`authorization_requested_image_count_mismatch`，已入 allowlist + KNOWN_ERROR_CODES）；
`requested_network_calls == 0`；bytes/wall_time 由 caller 提供受控 metadata（B2-B0
不預先載入 bytes）。

## 8. NetworkPolicy Deny-All

validate + assert_network_disabled 皆須通過；任何違反（allow_network=True、
destinations 非空、proxy/private/loopback/link-local/metadata 啟用、budget 非 0）→
authorized=False + 固定碼。**即使 context 全合法**，非 deny-all 即 blocked。

## 9. Audit v3 Wiring

wrapper 最終 audit 一律 v3（寫入 `audit_dir/v3/<run_id>/audit.json`；不覆寫 B1
engine 的 v2）。欄位來源：flag/env/context/decision/digest 來自 input+decision、
network_policy_version 來自經驗證 policy、requested_network_call_count 為實際值、
allowed_network_call_count=0、fixed_error_codes 保序唯一 strict allowlist。
context 不存在 → present=False、valid=False、decision=False、digest=""。

## 10. Blocked Flow

decision 任一錯誤 → 不呼叫 B1 engine（loader/adapter/cache 零呼叫）→ 寫 v3
blocked audit（失敗 → summary 加 `audit_write_failed`，仍 blocked）→ blocked summary。

## 11. Authorized Fake Flow

decision.authorized=True → 委派 B1 engine（fake/in-memory loader + fake adapter，
寫 v2 audit + cache）→ wrapper 寫最終 v3 audit；**v3 寫失敗 → status 降級
completed_with_failures/failed + `audit_write_failed`（不刪已成功 cache）**。

## 12. Zero-Network Proof

socket.socket/create_connection/getaddrinfo、urllib.urlopen、HTTPConnection 全封鎖下
執行 authorized/blocked 全流程 → network call count = 0（測試驗證）。

## 13. Zero-Secret-Env Proof

os.getenv / os.environ.get / os.environ.__getitem__ 監控 KEY/TOKEN/COOKIE/SECRET/
ENDPOINT/PASSWORD/PROXY → read count = 0（monkeypatch 自動還原）。

## 14. B1 Compatibility

B1 legacy engine（`execute_external_analyzer_plan`）、Audit v2 validator、cache
accounting、run-ID、loader/adapter/cache containment、determinism **全部不變**
（策略 A：舊 engine 保持 v2、新 wrapper 用 v3）；B1 94 tests 全綠。

## 15. Readiness Freeze

維持 SHADOW_READY + reasons 三碼；anonymized_real=0；baseline 未重新生成；
不得出現 external_analyzer_ready / real_dataset_validated / thresholds_met /
SAFE_PILOT / SAFE_PILOT_CANDIDATE。

## 16–17. 尚未接入

**Real adapter 尚未接入**（僅 fake adapter）；**Real network 尚未允許**
（deny-all 唯一）。

## 18. Phase 6.4C2-B2-B0.1 強化

1. **caller plan.authorized 完全不影響 wrapper**：True/False 皆有測試證明
   （test_plan_authorized_true_false_semantics_consistent——核心語意一致）；
   唯一授權來源 = 合併後 AuthorizationDecision.authorized
2. **內部 safe plan**：final decision authorized 後以 `dataclasses.replace`
   建立 `authorized=True/status="planned"/dry_run=False` 副本傳入 B1 engine；
   caller 原始 plan 執行前後未修改（測試驗證）；不使用 object.__setattr__
3. **plan structural preflight**：`validate_authorized_execution_plan`（17 項：
   schema/run_id/created_at/status/counts/keys/indexes/hashes/duplicate/
   adapter identity/requested counts/expected_run_id）；任一失敗 →
   authorized=False + blocked v3 audit + 零呼叫
4. **expected_run_id 雙重 binding**：`input.expected_run_id == plan.run_id`
   （新碼 authorization_expected_run_id_mismatch）AND
   `context.approved_run_id == plan.run_id`（binding 碼）
5. **invalid NetworkPolicy sentinel**：`_safe_network_policy_version` →
   policy None/錯誤型別/unsafe version 一律 `"invalid-policy"`（不回顯 caller
   值）；blocked Audit v3 仍成功寫出、不誤報 audit_write_failed
6. **final decision 合併**：context decision + plan preflight errors 合併；
   plan error → authorization_decision=False（audit 一致性測試）
7. **執行順序**：started → run ID → policy → context → plan preflight →
   合併 → blocked（v3）或 safe plan → 委派 → 最終 v3；第 8 步前零呼叫

## 19. Phase 6.4C2-B2-B0.2 強化

1. **malformed 頂層輸入**：plan=None/object()/str、authorization_input=None/
   object()、eligible_cases=None/str/[object()]——任何屬性存取前型別判斷 →
   blocked（不 crash、零呼叫、成功寫合法 Audit v3、不誤報 audit_write_failed）
2. **SafeEligibleFacts + collect_safe_eligible_facts**：list/tuple + 每項
   EligibleAnalyzerCase + refs/hashes list 且長度一致 + hash 64-hex；
   invalid → valid=False/counts=0/hashes=()/固定碼；不讀 bytes、不存
   case ID/reference；generator 不因 malformed crash
3. **plan 欄位型別驗證**：12 個欄位先型別檢查（int 非 bool ≥0、str、list、
   bool）再使用；內容型別（case_keys 64-hex、indexes int、hashes 64-hex）；
   任何型別錯誤 → execution_plan_invalid（不 crash）
4. **blocked Audit 安全欄位來源**：counts/hashes 用 safe facts；
   planned_image_count 用 safe image count（拿不到=0）；run_id 用
   `_safe_plan_run_id`（錯誤 plan 仍得 run-<12hex>、不回顯 caller 值）
5. **Exception 邊界**：wrapper 只捕獲 AnalyzerAuditWriteError/ValueError
   （audit 寫入/驗證）；AttributeError/TypeError/programming bug 不被吞
6. **malformed input 零呼叫 proof**：6 種組合全 blocked、cache=0、
   無 audit_write_failed、Audit v3 合法

## 20. Phase 6.4C2-B2-B0.3 強化

1. **_safe_network_policy_version 修正（審核阻塞項）**：B0.2 的修改未寫入
   （try/except 殘留）；B0.3 移除全部 try/except，改為**嚴格 exact-type
   契約**：`type(policy) is NetworkPolicyV1`（subclass 拒絕——避免覆寫
   property/`__getattribute__` 在 security boundary 靜默擴張）→ schema →
   version 型別/長度/字元集顯式驗證；None/object()/str/subclass/惡意
   RaisingPolicy（property 會 raise）全回傳固定 `"invalid-policy"`
   （10 單元測試）
2. **AST 防退化**：涵蓋 7 個 B2-B0 security functions
   （_safe_network_policy_version / collect_safe_eligible_facts /
   _safe_plan_run_id / validate_authorized_execution_plan / _build_v3_audit /
   execute_authorized_external_analyzer_plan / _write_blocked_v3_audit）；
   禁止 bare except / except Exception / except BaseException / tuple 含
   Exception；允許例外僅 AnalyzerAuditWriteError/ValueError 於 audit 邊界；
   proof：checked=7、bare=0、Exception=0、BaseException=0、PASS
3. **誠實紀錄**：本輪 implementation report 明確記載上一輪遺漏（broad
   exception 殘留）與修正；不再聲稱未證明的內容

## 21. 下一階段人工 Gate

B2-B1（real adapter implementation）前需人工批准：① real adapter 契約定稿
② network policy 擴張（預設否）③ credentials 處理 ④ commit/push 授權。
