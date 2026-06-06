from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import quality_gate


def run_test_matrix(*, mode: str = "quick", json_report: str | Path | None = None) -> dict:
    started_at = quality_gate._utc_now()
    started = time.perf_counter()
    checks = [
        quality_gate._run_check("feature_matrix", quality_gate.validate_feature_matrix),
        quality_gate._run_check("cli_matrix", quality_gate.validate_cli_matrix),
        quality_gate._run_check("api_matrix", quality_gate.validate_api_matrix),
        quality_gate._run_check("examples_matrix", quality_gate.validate_examples_matrix),
        quality_gate._run_check("cli_matrix_smoke", lambda: quality_gate.run_cli_matrix(mode=mode)),
    ]
    status = "failed" if any(check["status"] == "failed" for check in checks) else "passed"
    report = {
        "status": status,
        "started_at": started_at,
        "finished_at": quality_gate._utc_now(),
        "duration_ms": quality_gate._elapsed_ms(started),
        "checks": checks,
        "summary": {
            "passed": sum(1 for check in checks if check["status"] == "passed"),
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "skipped": sum(1 for check in checks if check["status"] == "skipped"),
        },
    }
    if json_report:
        path = Path(json_report)
        if not path.is_absolute():
            path = quality_gate.ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the crawler platform test matrix.")
    parser.add_argument("--quick", action="store_true", help="Run quick matrix checks.")
    parser.add_argument("--full", action="store_true", help="Run full matrix checks.")
    parser.add_argument("--json-report", help="Write a JSON report to this path.")
    args = parser.parse_args(argv)
    if args.quick and args.full:
        parser.error("--quick and --full cannot be combined")
    mode = "full" if args.full else "quick"
    report = run_test_matrix(mode=mode, json_report=args.json_report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
