"""
Account creation automation for SocialLinks admin panel.
Usage: python create_accounts.py [--csv clients.csv] [--dry-run]

CSV columns:
  product;full_name;Email;Company name;license_type;start_date;end_date;modes;packs;company_id;points
  packs      — optional, comma-separated Billing template names, each optionally
               with ':start:end', e.g. 'API DN Trial,SMP Trial:2026-01-01:2026-06-01'
               (names as in Billing's template select, see packs.BILLING_TEMPLATES)
               dates default to the row's own start_date/end_date if omitted
  company_id — optional integer, defaults to 0

This module is the CLI only: argparse plus reading/writing clients.csv and the
key/token JSON files. The three-phase run itself lives in runner.py (shared
with server.py); per-system automation in crimewall.py, billing.py, slapi.py;
CSV parsing in models.py; pack template registry in packs.py.
"""

import argparse
import json
import sys
from pathlib import Path

import runner
from models import load_clients

PACKS_KEYS_FILE = Path("packs_keys.json")
SLAPI_TOKENS_FILE = Path("slapi_tokens.json")


def main() -> None:
    # Windows consoles default to cp1251/cp1252 and crash on names like "Peña".
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="clients.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--phase",
        choices=["1", "2", "3", "all"],
        default="all",
        help="1=accounts (CrimeWall + SL API)  2=Billing keys+packs  3=CrimeWall pack keys  all=all phases",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] File not found: {csv_path}")
        sys.exit(1)

    clients = load_clients(str(csv_path))
    if not clients:
        print("[ERROR] No valid clients found in CSV.")
        sys.exit(1)

    print(f"Loaded {len(clients)} client(s) from {csv_path}")
    phases = set(runner.ALL_PHASES) if args.phase == "all" else {args.phase}

    result = runner.RunResult()
    # Phase 3 without phase 2 in this invocation: use the keys from the last phase 2 run.
    if "3" in phases and "2" not in phases and PACKS_KEYS_FILE.exists():
        result.packs_keys = json.loads(PACKS_KEYS_FILE.read_text(encoding="utf-8"))
        print(f"[PHASE 3] Loaded keys from {PACKS_KEYS_FILE}")

    try:
        runner.run(clients, phases=phases, dry_run=args.dry_run, result=result)
    finally:
        # Saved even if a later phase crashed, so a re-run can pick up from here.
        if result.slapi_tokens:
            SLAPI_TOKENS_FILE.write_text(json.dumps(result.slapi_tokens, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [SAVED] SL API tokens -> {SLAPI_TOKENS_FILE}")
        if result.packs_keys and "2" in phases:
            PACKS_KEYS_FILE.write_text(json.dumps(result.packs_keys, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  [SAVED] keys -> {PACKS_KEYS_FILE}")


if __name__ == "__main__":
    main()
