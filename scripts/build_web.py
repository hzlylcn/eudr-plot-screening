"""Build the data for the MapLibre demo map in web/.

1. Runs the screening (fast, ~30 s) -> web/data/plots.geojson
2. With --thumbs: Sentinel-2 before/after thumbnails per plot
   -> web/data/thumbs/<plot_id>_{before,after}.jpg and NDVI inside the plot.
   This reads imagery over HTTP and takes ~1-2 min per plot; existing
   thumbnails are skipped, so the run can be resumed.

Usage:
    python scripts/build_web.py            # screening only
    python scripts/build_web.py --thumbs   # + thumbnails
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from eudr_screen import screen  # noqa: E402
from eudr_screen import sentinel2 as s2  # noqa: E402

WEB_DATA = ROOT / "web" / "data"
THUMBS = WEB_DATA / "thumbs"
PERIODS = {"before": ("2019-11-01", "2020-03-31"), "after": ("2025-11-01", "2026-03-31")}
PAD = 0.004  # ~450 m around the plot centre
KEEP = ["plot_id", "commodity", "declared_area_ha", "geom_input", "area_ha",
        "jrc_forest_2020_share", "treecover2000_mean", "loss_post2020_ha",
        "loss_post2020_share", "loss_years", "flag", "flag_reason", "synthetic"]


def thumbnail(plot_id: str, geom, out_dir: Path) -> dict:
    c = geom.centroid
    bbox = (c.x - PAD, c.y - PAD, c.x + PAD, c.y + PAD)
    utm = gpd.GeoSeries([geom], crs=4326).estimate_utm_crs()
    plot_utm = gpd.GeoSeries([geom], crs=4326).to_crs(utm)
    stats = {}
    for label, (a, b) in PERIODS.items():
        path = out_dir / f"{plot_id}_{label}.jpg"
        items = s2.search(bbox, a, b, max_cloud=40)
        comp = s2.median_composite(s2.load_cube(items, bbox, crs=str(utm))).compute()
        nd = s2.ndvi(comp).rio.write_crs(utm)
        stats[f"ndvi_{label}"] = round(float(nd.rio.clip(plot_utm.geometry, drop=True).mean()), 3)
        stats[f"scenes_{label}"] = len(items)

        fig, ax = plt.subplots(figsize=(3, 3), dpi=100)
        ext = [float(comp.x.min()), float(comp.x.max()), float(comp.y.min()), float(comp.y.max())]
        ax.imshow(s2.rgb(comp), extent=ext)
        plot_utm.boundary.plot(ax=ax, color="yellow", linewidth=2)
        ax.set_axis_off()
        fig.subplots_adjust(0, 0, 1, 1)
        fig.savefig(path, format="jpg", pil_kwargs={"quality": 85})
        plt.close(fig)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thumbs", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    import rioxarray  # noqa: F401  (registers .rio)

    plots = gpd.read_file(ROOT / "data" / "demo_plots.geojson")
    print(f"Screening {len(plots)} plots ...", flush=True)
    res = screen.screen(plots)
    res = res[KEEP + ["geometry"]].copy()
    for col in ("area_ha", "jrc_forest_2020_share", "treecover2000_mean",
                "loss_post2020_ha", "loss_post2020_share"):
        res[col] = res[col].round(3)

    stats_path = WEB_DATA / "thumb_stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    if args.thumbs:
        THUMBS.mkdir(parents=True, exist_ok=True)
        todo = [(r.plot_id, r.geometry) for r in res.itertuples()
                if not ((THUMBS / f"{r.plot_id}_after.jpg").exists() and r.plot_id in stats)]
        print(f"Thumbnails: {len(todo)} to build", flush=True)
        with ThreadPoolExecutor(args.workers) as pool:
            futs = {pool.submit(thumbnail, pid, g, THUMBS): pid for pid, g in todo}
            for f in as_completed(futs):
                pid = futs[f]
                try:
                    stats[pid] = f.result()
                    print(f"  {pid}: {stats[pid]}", flush=True)
                except Exception as e:  # keep going; the map handles missing thumbs
                    print(f"  {pid}: FAILED {e!r}", flush=True)
                stats_path.write_text(json.dumps(stats, indent=1, sort_keys=True))

    for k in ("ndvi_before", "ndvi_after"):
        res[k] = [stats.get(pid, {}).get(k) for pid in res.plot_id]
    res["has_thumbs"] = [(THUMBS / f"{pid}_after.jpg").exists() for pid in res.plot_id]

    WEB_DATA.mkdir(parents=True, exist_ok=True)
    out = WEB_DATA / "plots.geojson"
    out.write_text(res.to_json(drop_id=True))
    print(f"Wrote {out.relative_to(ROOT)}  flags: {res.flag.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
