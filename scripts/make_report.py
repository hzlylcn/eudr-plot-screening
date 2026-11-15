"""Build the PDF screening report from the web map data.

Run scripts/build_web.py --thumbs first (it writes web/data/plots.geojson
and the Sentinel-2 thumbnails this report uses).

Usage:  python scripts/make_report.py [--out outputs/eudr_screening_report.pdf]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eudr_screen import report  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "outputs" / "eudr_screening_report.pdf"))
    args = ap.parse_args()
    res = gpd.read_file(ROOT / "web" / "data" / "plots.geojson")
    res["loss_years"] = res["loss_years"].fillna("")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report.build(res, ROOT / "web" / "data" / "thumbs", out)
    print(f"Wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    main()
