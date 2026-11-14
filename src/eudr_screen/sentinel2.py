"""Sentinel-2 L2A access via the Earth Search STAC API (AWS open data).

No account needed. Data are read straight from cloud-optimised GeoTIFFs,
only for the requested area.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
import os

from odc.stac import configure_rio, load
from pystac_client import Client

# Robust HTTP reads: retry transient S3 errors instead of failing the run.
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
configure_rio(cloud_defaults=True)

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# Scene Classification Layer (SCL) classes we keep:
# 4 vegetation, 5 bare soil, 6 water, 7 unclassified.
# Dropped: 0 no data, 1 saturated, 2 dark area, 3 cloud shadow,
# 8/9 cloud medium/high probability, 10 thin cirrus, 11 snow.
SCL_CLEAR = (4, 5, 6, 7)

BANDS = ["blue", "green", "red", "nir", "swir16", "scl"]


def search(bbox, start: str, end: str, max_cloud: int = 60):
    """Return STAC items intersecting bbox (lon_min, lat_min, lon_max, lat_max)."""
    client = Client.open(STAC_URL)
    result = client.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
    )
    return dedupe(list(result.items()))


def _baseline(item) -> float:
    try:
        return float(item.properties.get("s2:processing_baseline", "0"))
    except ValueError:
        return 0.0


def dedupe(items):
    """The archive holds several versions of some scenes (original and
    reprocessed). Keep only the newest processing baseline per tile and day."""
    best = {}
    for it in items:
        tile = it.properties.get("grid:code") or it.properties.get("s2:mgrs_tile")
        key = (tile, it.datetime.date())
        if key not in best or _baseline(it) > _baseline(best[key]):
            best[key] = it
    return sorted(best.values(), key=lambda i: i.datetime)


def _needs_offset(item) -> bool:
    """From processing baseline 04.00, ESA stores reflectance with a +1000
    offset. Earth Search may already have removed it
    (earthsearch:boa_offset_applied). Older baselines never had an offset."""
    if _baseline(item) < 4.0:
        return False
    return not item.properties.get("earthsearch:boa_offset_applied", False)


def load_cube(items, bbox, resolution: int = 10, crs: str | None = None) -> xr.Dataset:
    """Load items into an xarray cube (time, y, x) with surface reflectance 0–1."""
    ds = load(
        items,
        bands=BANDS,
        bbox=bbox,
        resolution=resolution,
        crs=crs or "utm",
        groupby="solar_day",
        chunks={},
        fail_on_error=False,  # skip a scene that cannot be read, keep the rest
    )
    # Per-time offset flag (after solar_day grouping, items share a day)
    by_day = {}
    for it in items:
        day = np.datetime64(it.datetime.replace(tzinfo=None), "D")
        by_day[day] = by_day.get(day, False) or _needs_offset(it)
    offset = xr.DataArray(
        [1000.0 if by_day.get(np.datetime64(t, "D"), False) else 0.0 for t in ds.time.values],
        dims="time",
        coords={"time": ds.time},
    )
    for b in BANDS:
        if b == "scl":
            continue
        raw = ds[b].where(ds[b] > 0)
        ds[b] = ((raw - offset) / 10000.0).clip(0, 1)
    return ds


def mask_clouds(ds: xr.Dataset) -> xr.Dataset:
    clear = ds["scl"].isin(SCL_CLEAR)
    return ds.drop_vars("scl").where(clear)


def median_composite(ds: xr.Dataset) -> xr.Dataset:
    """Cloud-masked median over time, plus count of clear observations."""
    masked = mask_clouds(ds)
    comp = masked.median("time", skipna=True)
    comp["clear_obs"] = masked["red"].notnull().sum("time")
    return comp


def ndvi(ds: xr.Dataset) -> xr.DataArray:
    return ((ds["nir"] - ds["red"]) / (ds["nir"] + ds["red"])).rename("ndvi")


def rgb(ds: xr.Dataset, gain: float = 3.5) -> np.ndarray:
    """True-colour image as (y, x, 3) array in 0–1 for plotting."""
    arr = np.stack([ds["red"].values, ds["green"].values, ds["blue"].values], axis=-1)
    return np.clip(arr * gain, 0, 1)
