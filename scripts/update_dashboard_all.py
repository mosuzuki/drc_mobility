#!/usr/bin/env python3
"""Run all dashboard update steps in the correct order.

Downstream estimates are regenerated even when no new SitRep is found, so the
JSON files cannot remain stale when report_summary.csv has already advanced.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / ".dashboard_update_all_status.md"

STEPS = [
    ("INSP SitRep update", [sys.executable, "scripts/update_from_insp_sitrep.py"], False),
    ("Repair latest SitRep values", [sys.executable, "scripts/repair_latest_sitrep_values.py"], True),
    ("Uganda EVD update", [sys.executable, "scripts/update_uganda_evd.py"], False),
    ("Health-zone activity status", [sys.executable, "scripts/generate_health_zone_activity_status.py"], True),
    ("Latest situation summary", [sys.executable, "scripts/generate_latest_situation.py"], True),
    ("Final size projection", [sys.executable, "scripts/generate_final_size_projection.py"], True),
    ("Reporting-adjusted infection estimate", [sys.executable, "scripts/estimate_true_infections.py"], True),
    ("Reported-case Rt estimates", [sys.executable, "scripts/generate_rt_estimates.py"], True),
    ("Delay-adjusted CFR estimates", [sys.executable, "scripts/generate_cfr_estimates.py"], True),
    ("Dashboard output validation", [sys.executable, "scripts/validate_dashboard_outputs.py"], True),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-sitrep", action="store_true", help="Regenerate downstream outputs without fetching INSP SitRep again")
    args = ap.parse_args()
    lines = ["# Dashboard integrated update", "", f"Started at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]
    hard_failed = False
    steps = STEPS[1:] if args.skip_sitrep else STEPS
    for name, cmd, required in steps:
        lines.append(f"## {name}")
        try:
            proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=900)
        except Exception as exc:
            lines.append(f"- Exception: {exc}")
            if required:
                hard_failed = True
                break
            continue
        lines.append(f"- Exit code: {proc.returncode}")
        if proc.stdout.strip():
            lines.append("- stdout:")
            lines.append("```")
            lines.append(proc.stdout.strip()[-3000:])
            lines.append("```")
        if proc.stderr.strip():
            lines.append("- stderr:")
            lines.append("```")
            lines.append(proc.stderr.strip()[-3000:])
            lines.append("```")
        if proc.returncode != 0 and required:
            hard_failed = True
            break
        lines.append("")
    lines.append(f"Finished at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if hard_failed:
        print(STATUS.read_text(encoding="utf-8"))
        raise SystemExit(1)
    print(STATUS.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
