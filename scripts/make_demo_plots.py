"""Create SYNTHETIC demo plots for the screening tool.

Plots are placed with a known "expected answer" using Hansen data, so the
tool can be checked against it:

  post2020_loss   forest in 2000, cleared 2021+       -> should be flagged
  pre2020_loss    cleared 2001-2020, nothing since    -> before cut-off, low risk
  stable_forest   dense cover, no loss                -> low risk (still forest)
  no_forest       low cover since 2000, no loss       -> low risk

Points stand for plots < 4 ha, squares (~6.25 ha) for plots >= 4 ha,
mirroring the EUDR geolocation rule. These are NOT real farms.

Usage:  python scripts/make_demo_plots.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from eudr_screen import hansen  # noqa: E402

# South-west Côte d'Ivoire: Taï National Park edge and surrounding cocoa belt
AOI = (-7.6, 5.4, -6.6, 6.4)
CELL = 8  # pixels per candidate cell (~240 m)
SEED = 42
N_PER_CASE = {"post2020_loss": 7, "pre2020_loss": 4, "stable_forest": 4, "no_forest": 5}
OUT = ROOT / "data" / "demo_plots.geojson"


def cell_stats(lossyear, cover):
    h, w = lossyear.shape
    h, w = h - h % CELL, w - w % CELL
    ly = lossyear[:h, :w].reshape(h // CELL, CELL, w // CELL, CELL)
    cv = cover[:h, :w].reshape(h // CELL, CELL, w // CELL, CELL)
    post = (ly > 20).mean(axis=(1, 3))
    pre = ((ly > 0) & (ly <= 20)).mean(axis=(1, 3))
    cover_mean = cv.mean(axis=(1, 3))
    return post, pre, cover_mean


def main():
    print("Reading Hansen lossyear / treecover2000 window ...")
    lossyear, tr = hansen.read_window("lossyear", AOI)
    cover, _ = hansen.read_window("treecover2000", AOI)
    post, pre, cov = cell_stats(lossyear, cover)

    rules = {
        "post2020_loss": (post > 0.25) & (cov > 50),
        "pre2020_loss": (pre > 0.5) & (post == 0),
        "stable_forest": (cov > 80) & (post == 0) & (pre == 0),
        "no_forest": (cov < 15) & (post == 0) & (pre == 0),
    }

    rng = np.random.default_rng(SEED)
    features = []
    pid = 1
    for case, n in N_PER_CASE.items():
        rows, cols = np.nonzero(rules[case])
        print(f"{case}: {len(rows)} candidate cells")
        if len(rows) == 0:
            continue
        # spread picks: avoid neighbours by sampling without replacement
        idx = rng.choice(len(rows), size=min(n, len(rows)), replace=False)
        for k, i in enumerate(idx):
            r, c = rows[i], cols[i]
            # cell centre in lon/lat
            x0, y0 = tr * (c * CELL, r * CELL)
            x1, y1 = tr * ((c + 1) * CELL, (r + 1) * CELL)
            lon, lat = (x0 + x1) / 2, (y0 + y1) / 2
            as_point = k % 2 == 0  # alternate points / polygons
            if as_point:
                geom = {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}
                area = 2.5
            else:
                half = 0.001125  # ~125 m -> ~6.25 ha square
                ring = [
                    [lon - half, lat - half], [lon + half, lat - half],
                    [lon + half, lat + half], [lon - half, lat + half],
                    [lon - half, lat - half],
                ]
                geom = {"type": "Polygon", "coordinates": [[[round(a, 6), round(b, 6)] for a, b in ring]]}
                area = 6.25
            features.append({
                "type": "Feature",
                "properties": {
                    "plot_id": f"DEMO-{pid:03d}",
                    "declared_area_ha": area,
                    "commodity": "cocoa",
                    "expected_case": case,
                    "synthetic": True,
                },
                "geometry": geom,
            })
            pid += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=1))
    print(f"Wrote {len(features)} plots -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
