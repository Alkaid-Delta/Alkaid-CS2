# Alkaid-CS2 Phase 0 Regression Baseline

- 產生時間: 2026-08-01T02:34:08.676566+00:00
- 分支: `agent/v2-architecture-baseline`
- Commit: `3eb8fe2`
- Fixture 數: 13

## pytest 結果

```
=================== 5 passed, 1 skipped, 7 xfailed in 0.23s ===================
```

| 案例 | 結果 | 已知缺陷 |
|------|------|---------|
| simple_single_twd | PASSED | `—` |
| legacy_single_nocts | PASSED | `—` |
| redline_vulcan_simplified | XFAIL | `first_match_return: only one item returned, order-dependent` |
| redline_vulcan_traditional | XFAIL | `traditional_variant_missing: 紅線 not in pattern_cn_to_en` |
| seller_ask_plus_buff_floor | XFAIL | `price_role_not_distinguished: buff_floor treated as same price class` |
| rmb_price_no_conversion_marker | XFAIL | `currency_lost_on_dict_hit: RMB not marked, no conversion` |
| validation_failure_returns_first | XFAIL | `returns_unverified_first_result: L562 returns data after 2 failures` |
| multi_image_second_has_price | SKIPPED | `first_image_break: only first successful image processed, price lost` |
| stat_trak_ak | PASSED | `—` |
| knife_star_prefix | PASSED | `—` |
| buying_post_nocts | XFAIL | `role_not_distinguished: legacy has no ItemRole, buying treated as selling` |
| trade_only_post | XFAIL | `role_not_distinguished: legacy has no ItemRole, trade treated as selling` |
| no_price_selling_post | PASSED | `—` |

## 已知失敗（known failures）

- **redline_vulcan_simplified**: first_match_return: only one item returned, order-dependent
- **redline_vulcan_traditional**: traditional_variant_missing: 紅線 not in pattern_cn_to_en
- **seller_ask_plus_buff_floor**: price_role_not_distinguished: buff_floor treated as same price class
- **rmb_price_no_conversion_marker**: currency_lost_on_dict_hit: RMB not marked, no conversion
- **validation_failure_returns_first**: returns_unverified_first_result: L562 returns data after 2 failures
- **multi_image_second_has_price**: first_image_break: only first successful image processed, price lost
- **buying_post_nocts**: role_not_distinguished: legacy has no ItemRole, buying treated as selling
- **trade_only_post**: role_not_distinguished: legacy has no ItemRole, trade treated as selling

## Metrics（Phase 1 起逐項填寫）

```json
{
  "item_exact_match": null,
  "seller_price_exact_match": null,
  "currency_accuracy": null,
  "item_price_link_accuracy": null,
  "unresolved_rate": null,
  "false_positive_deal_count": null,
  "avg_latency_ms": null,
  "flash_pro_ratio": null,
  "model_cost_per_100_posts": null
}
```
