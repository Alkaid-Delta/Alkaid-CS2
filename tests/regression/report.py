"""
report.py — Phase 0 baseline report generator
==============================================
Runs the golden regression suite and emits a structured report:

  tests/regression/reports/baseline_report.json
  tests/regression/reports/baseline_report.md

The report records current parser behavior WITHOUT modifying production code.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")


def run_pytest() -> dict:
    """執行 golden 測試並解析結果（用 -v 以取得 per-test 結果）"""
    cmd = [
        sys.executable, "-m", "pytest",
        os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
        "-v", "--tb=short", "--no-header",
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", PROJECT_ROOT)
    # 若環境有 DEEPSEEK_API_KEY 則帶入（validation fixture 才能跑）
    if os.environ.get("DEEPSEEK_API_KEY"):
        env["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    stdout = proc.stdout + proc.stderr

    # 解析 summary（最後一行）
    summary_line = ""
    for line in reversed(stdout.strip().splitlines()):
        if "passed" in line or "failed" in line or "xfail" in line or "skipped" in line:
            summary_line = line.strip()
            break

    # 解析每個 test 的結果（xxx PASSED/FAILED/XFAIL 行）
    per_test = {}
    for line in stdout.splitlines():
        for marker in ("PASSED", "FAILED", "XFAIL", "SKIPPED"):
            if marker in line and "test_golden_posts" in line:
                # 例: tests/regression/test_golden_posts.py::test_simple_single_twd PASSED
                parts = line.strip().split("::")
                if len(parts) >= 2:
                    name = parts[-1].split()[0]
                    per_test[name] = marker
                break

    return {
        "exit_code": proc.returncode,
        "summary": summary_line,
        "per_test": per_test,
        "raw_tail": "\n".join(stdout.strip().splitlines()[-25:]),
    }


def load_fixtures() -> tuple[list, dict]:
    fx_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    with open(os.path.join(fx_dir, "posts.json"), encoding="utf-8") as f:
        posts = json.load(f)
    with open(os.path.join(fx_dir, "expected.json"), encoding="utf-8") as f:
        expected = json.load(f)
    return posts, expected


def build_report() -> dict:
    posts, expected = load_fixtures()
    pytest_result = run_pytest()

    cases = []
    for p in posts:
        exp = expected.get(p["id"], {})
        cases.append({
            "id": p["id"],
            "notes": p.get("notes", ""),
            "known_defect": p.get("known_defect"),
            "expected_status": exp.get("status"),
            "requires_api_key": p.get("requires_api_key", False),
            "requires_crawler": p.get("requires_crawler", False),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "branch": _git_branch(),
        "commit": _git_head(),
        "suite": pytest_result,
        "fixture_count": len(posts),
        "cases": cases,
        "metrics": {
            "item_exact_match": None,   # Phase 1 起逐步填
            "seller_price_exact_match": None,
            "currency_accuracy": None,
            "item_price_link_accuracy": None,
            "unresolved_rate": None,
            "false_positive_deal_count": None,
            "avg_latency_ms": None,
            "flash_pro_ratio": None,
            "model_cost_per_100_posts": None,
        },
    }


def _git_branch() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        ).stdout.strip()
    except Exception:
        return "unknown"


def render_markdown(report: dict) -> str:
    lines = [
        "# Alkaid-CS2 Phase 0 Regression Baseline",
        "",
        f"- 產生時間: {report['generated_at']}",
        f"- 分支: `{report['branch']}`",
        f"- Commit: `{report['commit']}`",
        f"- Fixture 數: {report['fixture_count']}",
        "",
        "## pytest 結果",
        "",
        f"```\n{report['suite']['summary']}\n```",
        "",
        "| 案例 | 結果 | 已知缺陷 |",
        "|------|------|---------|",
    ]
    per_test = report["suite"]["per_test"]
    for c in report["cases"]:
        # 對應 test 函式名 = test_ + fixture id
        result = per_test.get("test_" + c["id"], "NOT RUN")
        defect = c["known_defect"] or "—"
        lines.append(f"| {c['id']} | {result} | `{defect}` |")

    lines += [
        "",
        "## 已知失敗（known failures）",
        "",
    ]
    for c in report["cases"]:
        if c["known_defect"]:
            lines.append(f"- **{c['id']}**: {c['known_defect']}")
    lines += [
        "",
        "## Metrics（Phase 1 起逐項填寫）",
        "",
        "```json",
        json.dumps(report["metrics"], indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    return "\n".join(lines)


def main():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    report = build_report()

    jpath = os.path.join(REPORTS_DIR, "baseline_report.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    mpath = os.path.join(REPORTS_DIR, "baseline_report.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))

    print(json.dumps(report["suite"], ensure_ascii=False, indent=2))
    print(f"\n📄 報告已寫入:\n  {jpath}\n  {mpath}")


if __name__ == "__main__":
    main()
