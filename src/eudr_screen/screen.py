"""Per-plot EUDR screening metrics from open forest datasets.

Question 1: was the plot forest at the cut-off?  -> JRC GFC2020 (10 m)
Question 2: was tree cover lost after 2020?       -> Hansen GFC lossyear (30 m)

The two datasets use different forest definitions and can disagree; a
disagreement is reported as its own flag instead of being hidden.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

from . import hansen

JRC_URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/FOREST/GFC2020/LATEST/"
    "single-cog/JRC_GFC2020_V4_COG.tif"
)  # 1 = forest on 31 Dec 2020, 0 = not forest. CC BY 4.0, (c) European Union

# Screening thresholds (tunable, documented in the report)
MIN_LOSS_HA = 0.2          # ignore isolated single-pixel noise (1 Hansen px ~ 0.09 ha)
MIN_LOSS_SHARE = 0.05      # or >= 5 % of the plot
MIN_JRC_FOREST_SHARE = 0.10  # plot counts as "had forest in 2020"

FLAG_LOW = "low_risk"
FLAG_REVIEW = "review"
FLAG_CONFLICT = "review_data_conflict"


def plot_geometries(plots: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Points (< 4 ha plots) become circles of the declared area; polygons stay.

    Returns WGS84 geometries plus `area_ha` measured in a local UTM projection.
    """
    utm = plots.estimate_utm_crs()
    g = plots.to_crs(utm)
    is_pt = g.geometry.geom_type == "Point"
    radius = np.sqrt(g.loc[is_pt, "declared_area_ha"] * 10_000 / np.pi)
    g.loc[is_pt, "geometry"] = g.loc[is_pt].geometry.buffer(radius)
    g["area_ha"] = g.geometry.area / 10_000
    g["geom_input"] = np.where(is_pt, "point", "polygon")
    return g.to_crs(4326)


def _read_masked(src, geom, pad=0.0005):
    """Read pixels whose centre falls inside geom. Returns 1-D array."""
    minx, miny, maxx, maxy = geom.bounds
    win = from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad, transform=src.transform)
    win = win.round_offsets().round_lengths()
    arr = src.read(1, window=win)
    inside = geometry_mask([geom], out_shape=arr.shape, transform=src.window_transform(win), invert=True)
    return arr[inside]


def screen(plots: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    g = plot_geometries(plots)
    rows = []
    env = rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5",
                       GDAL_HTTP_RETRY_DELAY="2")
    with env, rasterio.open("/vsicurl/" + JRC_URL) as jrc:
        hansen_srcs: dict[str, rasterio.DatasetReader] = {}
        try:
            for _, p in g.iterrows():
                c = p.geometry.centroid
                for layer in ("lossyear", "treecover2000"):
                    key = layer + hansen.tile_name(c.x, c.y)
                    if key not in hansen_srcs:
                        hansen_srcs[key] = rasterio.open("/vsicurl/" + hansen.url(layer, c.x, c.y))
                ly = _read_masked(hansen_srcs["lossyear" + hansen.tile_name(c.x, c.y)], p.geometry)
                tc = _read_masked(hansen_srcs["treecover2000" + hansen.tile_name(c.x, c.y)], p.geometry)
                jf = _read_masked(jrc, p.geometry)

                post = hansen.post_cutoff_loss(ly)
                share_post = float(post.mean()) if ly.size else 0.0
                years = sorted({int(y) + 2000 for y in ly[post]})
                rows.append({
                    "jrc_forest_2020_share": float((jf == 1).mean()) if jf.size else np.nan,
                    "treecover2000_mean": float(tc.mean()) if tc.size else np.nan,
                    "loss_post2020_share": share_post,
                    "loss_post2020_ha": share_post * p.area_ha,
                    "loss_pre2021_share": float(((ly > 0) & ~post).mean()) if ly.size else 0.0,
                    "loss_years": ",".join(map(str, years)),
                    "n_px_hansen": int(ly.size),
                    "n_px_jrc": int(jf.size),
                })
        finally:
            for s in hansen_srcs.values():
                s.close()

    out = pd.concat([g.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    out["flag"] = out.apply(_flag, axis=1)
    out["flag_reason"] = out.apply(_reason, axis=1)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=4326)


def _has_loss(r) -> bool:
    return r.loss_post2020_ha >= MIN_LOSS_HA or r.loss_post2020_share >= MIN_LOSS_SHARE


def _had_forest(r) -> bool:
    return r.jrc_forest_2020_share >= MIN_JRC_FOREST_SHARE


def _flag(r) -> str:
    if not _has_loss(r):
        return FLAG_LOW
    return FLAG_REVIEW if _had_forest(r) else FLAG_CONFLICT


def _reason(r) -> str:
    if not _has_loss(r):
        if r.loss_post2020_ha > 0:
            # EUDR has no minimum area: say so rather than hide it
            return (f"Minor tree-cover loss after 2020 ({r.loss_post2020_ha:.2f} ha, {r.loss_years}), "
                    f"below screening threshold ({MIN_LOSS_HA} ha / {MIN_LOSS_SHARE:.0%}). "
                    "Likely edge noise; spot-check recommended.")
        if _had_forest(r):
            return "No tree-cover loss after 2020; plot still largely forest per JRC 2020."
        return "No tree-cover loss after 2020."
    loss = f"{r.loss_post2020_ha:.2f} ha tree-cover loss after 2020 ({r.loss_years})"
    if _had_forest(r):
        return f"{loss}; JRC maps {r.jrc_forest_2020_share:.0%} of plot as forest in 2020."
    return (f"{loss}, but JRC 2020 maps only {r.jrc_forest_2020_share:.0%} as forest "
            "(possible tree crop / agroforestry). Datasets disagree: check imagery.")
