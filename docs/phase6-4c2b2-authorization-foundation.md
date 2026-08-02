# Phase 6.4C2-B2-A — Contract and Authorization Foundation

> **production mode = off** | **anonymized_real = 0** | **external analyzer executed = no**
> **real image bytes loaded = no** | **real_data_validation_status = insufficient**
> **intake_ready = null** | **readiness = SHADOW_READY**

## 1. B2-A 目標

建立 real-execution 前的最後契約層：AuthorizationContextV1、AuthorizationDecision、
flag/env/context 正式語意、Audit v3 schema、deny-all NetworkPolicyV1——全部離線、
零網路、零 secret-env 讀取。

## 2. 與 B1 的邊界

- B1：fake execution pipeline（已封版）——本階段**不改 B1 execution 行為**
- B2-A：只建立契約/授權/網路政策 foundation；Audit v3 只建 schema/validator，
  **不接到真實 execution**；B1 audit v2 writer 行為不變

## 3. AuthorizationContextV1

`authorization_context.py`：frozen dataclass（21 欄位）：
schema_version（固定 `authorization-context-v1`）、authorization_id（`auth-[0-9a-f]{16}`）、
authorization_scope（只允許 `evaluation`）、execution_mode（只允許 `contract_only`/`dry_run`；
**不得 production/live/real_execution**）、approved_at/expires_at（真實 datetime.strptime，
expires 須晚於 approved）、repository/branch/commit_sha（40-hex）、
dataset_manifest_sha256（64-hex）、approved_run_id（`run-[0-9a-f]{12}`）、
loader/adapter identity、adapter_config_sha256（64-hex）、network_policy_version、
budgets（全 int、bool 拒、>0；**max_network_calls 必須 0**）。

`validate_authorization_context(context, *, now_utc) -> list[str]`：固定錯誤碼、
保序、唯一、無動態值、deterministic。錯誤碼 allowlist 15 項（
authorization_context_missing / _invalid / _expired / authorization_scope_mismatch /
_execution_mode_invalid / _id_invalid / _run_id_invalid / _timestamp_invalid /
_expiry_not_after_approved / _commit_sha_invalid / _manifest_sha_invalid /
_config_sha_invalid / _budget_invalid / _network_budget_nonzero / _identity_invalid）。

## 4. AuthorizationDecision

frozen dataclass：authorization_flag_present / authorization_env_present /
authorization_env_accepted / authorization_context_present / authorization_context_valid /
authorized / authorization_context_digest / fixed_error_codes。

`evaluate_authorization(...)` 純函式：**authorized 由全部 gate 推導**（flag + env +
env accepted + context 有效 + 11 項 identity binding + 5 項 budget + requested
network calls == 0）；**不得讓 caller 傳 authorized=True；不得信任 plan.authorized**。

## 5. Flag／Env／Context 正式語意

- `authorization_flag_present`：CLI 是否明確帶入授權 flag
- `authorization_env_present`：指定 authorization gate env key 是否存在
- `authorization_env_accepted`：該 env value 是否精確符合允許值
- `authorization_context_present`：AuthorizationContext 是否存在
- `authorization_context_valid`：schema/expiry/identity/binding/budget 全通過
- `authorized`：以上所有 gate 的衍生結果

不得把 plan.authorized 當 flag present、固定寫 env_present=True、把 env 存在當
accepted、把 env accepted 當 context valid、記錄 env value 或 credential env。

## 6. Context Digest

`compute_authorization_context_digest`：受控 dict → `json.dumps(sort_keys,
separators=(",", ":"))` → SHA-256 → 64 位小寫 hex。不含 API key/token/cookie/
endpoint/email/username/storage reference/case ID。

## 7. Audit v3

`external-analyzer-audit-v3`：從 v2 延伸新增
authorization_env_accepted / authorization_context_present / authorization_context_valid /
authorization_decision / authorization_context_digest（64-hex）/
network_policy_version（非空安全字串）/ requested_network_call_count（==0）/
allowed_network_call_count（==0）。strict allowlist、unknown field 拒、bool 真正 bool、
count 非負 int（bool 拒）、fixed_error_codes allowlisted、privacy scan 通過
（digest 為受控 hash 欄位，掃描用 sanitized copy 避免 base64 heuristic 誤判）。

**v1/v2 的合法與非法案例結果保持不變**（v3 只新增分支）；本階段不把 v3 接到
真實 execution。

## 8. NetworkPolicyV1 deny-all

`network_policy.py`：frozen dataclass；B2-A 唯一合法模式
`schema_version=network-policy-v1`、`mode=deny_all`、allow_network=False、
allowed_destination_ids=空 tuple（**immutable，不用 list**）、
allow_redirects/proxy_env/private_ip/loopback/link_local/metadata_ip 全 False、
max_network_calls=0、max_concurrency=0、timeouts/bytes 全 0。

`validate_network_policy` + `assert_network_disabled`：11 個固定錯誤碼、
保序、唯一。**不得建 socket / DNS / import HTTP SDK / 讀 proxy 或 endpoint env**。

## 9. Zero-Network Guarantee

socket.socket / create_connection / getaddrinfo / urllib.urlopen /
http.client.HTTPConnection 全封鎖下執行 AuthorizationContext、AuthorizationDecision、
Audit v3、NetworkPolicy 全部主要流程——**network calls == 0**（測試驗證）。

## 10. Zero-Secret-Env Guarantee

os.getenv / os.environ.get / os.environ.__getitem__ 監控——名稱含
KEY/TOKEN/COOKIE/SECRET/ENDPOINT/PASSWORD/PROXY 的 env **讀取次數 == 0**（測試驗證）。

## 11–12. 尚未接入

**Real secure loader 尚未接入**（維持 InMemorySecureImageLoader）；
**Real adapter 尚未接入**（維持 FakeExternalAnalyzerAdapter）；無 cloud loader、
無 execute-real CLI。

## 13. Cache v2 尚未實作

analyzer_cache 維持 B1 契約（cache v1 + cache_key）；無 provenance 分欄位。

## 14. Real-Data Prerequisites

anonymized_real > 0（double-review 全通過）+ user 明確授權 real execution +
AuthorizationContext 建立 + network policy 核准，才可能進入 real 執行階段（B2-B 之後）。

## 15. Privacy 與 Logging

零落地 + redaction 沿用；authorization context 不含 credential；digest 不含敏感值；
audit 不記錄 env value。

## 16. Failure Containment

validate/evaluate 失敗 → 固定錯誤碼（無 exception 原文、無動態值）；
context 無效/過期/binding 不符 → authorized=False + 對應碼；network policy 違反
deny-all → 固定碼。

## 17. 測試矩陣（126 tests，B2-A.2 實數）

AST 實際收集：test_authorization_context.py = 83、test_network_policy.py = 17、
test_analyzer_audit_v3.py = 26、**total = 126**。

- AuthorizationContext 83（B2-A.1：requested budget 型別/負數 10、gate 型別 3、
  now_utc 3、context_valid 語意 8、allowlist 全覆蓋 3；B2-A.2：false-boolean
  gate 不無效化 context 1、gate type 三合一 order/unique/deterministic 1）
- NetworkPolicy 17（含明確 expected list 順序 1）
- Audit v3 26（含 state/digest 關係 9、union 固定碼 5）
- 另含 v1/v2 相容 4、zero-call/secret proof 2（在 authorization_context 83 內）

## 17.1 Phase 6.4C2-B2-A.1 強化

1. **Gate 型別**：authorization_flag_present/env_present/env_accepted 必須真正
   bool（拒 0/1/"true"/None/[]/{}）→ authorization_gate_type_invalid；
   decision 輸出的 gate 欄位永遠是真正 bool
2. **Requested budget**：五項 requested 值必須非負 int（bool 拒）→
   authorization_requested_budget_invalid + context_valid=False
3. **now_utc**：必須真正 UTC datetime（strptime）→ authorization_now_utc_invalid；
   無效時不比較 expiry、不誤報 expired；三時間戳 parse 成 datetime 比較
4. **context_valid 16 條件**：含 binding（11 項）與 budget（5 項超額 +
   network==0）；flag/env missing/rejected 不影響 context_valid
5. **Error code allowlist**：AUTHORIZATION_CONTEXT_ERROR_CODES /
   AUTHORIZATION_DECISION_ERROR_CODES / AUTHORIZATION_ALL_ERROR_CODES 公開；
   decision codes ⊆ ALL（測試全覆蓋）
6. **Audit v3 digest/state 關係**：context_present=True → digest 64-hex；
   False → digest 精確 ""；context_valid=True → present=True
   （authorization_context_state_invalid）；decision=True → 五 gate 全 True
   （authorization_decision_state_invalid）
7. **KNOWN_ERROR_CODES union**：analyzer_audit import
   AUTHORIZATION_ALL_ERROR_CODES + NETWORK_POLICY_ERROR_CODES（無文字漂移、
   無 circular import：authorization_context/network_policy 不 import analyzer_audit）
8. **NetworkPolicy 測試**：明確 expected list（9 碼順序固定，非自我參照排序）

## 17.2 Phase 6.4C2-B2-A.2 強化

1. **gate 型別 vs context_valid 契約**：context_valid=True 同時要求 gate_ok
   （三 gate 型別全合法）；gate 值 False（型別合法）不影響 context_valid
   （context_valid=True + authorized=False），gate 型別錯誤（1/"true"/None）
   使 context_valid=False + authorization_gate_type_invalid——兩者不得混淆
   （test_false_boolean_gates_do_not_invalidate_context 3 情境 +
   test_gate_type_error_order_unique_deterministic 三合一驗證）
2. **測試數以 AST 實收為準**（83/17/26=126）

## 18. Readiness Freeze

B2-A 不得提升 readiness：維持 SHADOW_READY + reasons
`insufficient_eligible_cases` / `no_real_cases_ingested` / `real_analyzer_not_run`；
不得出現 external_analyzer_ready / real_dataset_validated / thresholds_met /
SAFE_PILOT / SAFE_PILOT_CANDIDATE。

## 19. B2-B 人工批准 Gate

進入 B2-B 前需人工批准：① 是否允許把 AuthorizationContext/NetworkPolicy 接線到
execution ② 是否允許 Audit v3 寫入 ③ real adapter 契約定稿 ④ network policy 擴張
（預設否）⑤ commit/push 授權。

## 20. 允許與禁止修改範圍

允許：authorization_context.py / network_policy.py / docs / 3 測試檔 /
analyzer_audit.py（只新增 v3）/ external_analyzer_runner.py（只 helper，未改）。
禁止：production parser/crawler/bridge、secure_image_loader.py、
external_analyzer_adapter.py、analyzer_cache.py、scripts CLI、fixtures、baseline、
master。本階段未 commit / 未 push。
