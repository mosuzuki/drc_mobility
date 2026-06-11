"""
Template script for preparing dashboard CSV files from real mobility data.

This script does not download restricted or changing datasets automatically.
Place raw extracts under ./raw/ and adapt column names below.

Expected output files:
- data/outbreak_zones.csv
- data/destinations.csv
- data/monthly_flows.csv
- data/scenarios.csv
"""

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = ROOT / "raw"


def prepare_monthly_flows(raw_flow_path: str) -> pd.DataFrame:
    """Convert Flowminder/HDX-like OD data into dashboard schema.

    Required output columns:
    month, origin_id, destination_id, movement
    """
    df = pd.read_csv(raw_flow_path)

    # TODO: Adapt these column names to the actual Flowminder / HDX export.
    rename_map = {
        "origin_hz_id": "origin_id",
        "destination_hz_id": "destination_id",
        "date_month": "month",
        "estimated_movement": "movement",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    required = ["month", "origin_id", "destination_id", "movement"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns after renaming: {missing}")

    out = df[required].copy()
    out["movement"] = pd.to_numeric(out["movement"], errors="coerce").fillna(0).round().astype(int)
    return out


def main():
    DATA.mkdir(exist_ok=True)
    raw_flow = RAW / "flowminder_od.csv"
    if raw_flow.exists():
        flows = prepare_monthly_flows(str(raw_flow))
        flows.to_csv(DATA / "monthly_flows.csv", index=False)
        print(f"Wrote {DATA / 'monthly_flows.csv'}")
    else:
        print("No raw/flowminder_od.csv found. Keeping existing sample data.")


if __name__ == "__main__":
    main()
