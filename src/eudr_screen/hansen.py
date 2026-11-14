"""Hansen / UMD Global Forest Change (CC BY 4.0).

Tiles are 10x10 degrees at ~30 m. We read only the window we need over HTTP
(GDAL /vsicurl), so the full ~130 MB tile is never downloaded.

lossyear: 0 = no loss, n = loss in year 2000 + n (e.g. 21 -> 2021).
treecover2000: % canopy cover in 2000.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from rasterio.windows import from_bounds

VERSION = "GFC-2025-v1.13"  # loss up to and including 2025
BASE = "https://storage.googleapis.com/earthenginepartners-hansen"

EUDR_CUTOFF_YEAR = 2020  # loss in 2021 or later is "after the cut-off"


def tile_name(lon: float, lat: float) -> str:
    """Hansen tiles are named by their top-left corner, e.g. 10N_010W."""
    top = math.ceil(lat / 10) * 10
    left = math.floor(lon / 10) * 10
    ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
    ew = f"{abs(left):03d}{'E' if left >= 0 else 'W'}"
    return f"{ns}_{ew}"


def url(layer: str, lon: float, lat: float) -> str:
    return f"{BASE}/{VERSION}/Hansen_{VERSION}_{layer}_{tile_name(lon, lat)}.tif"


def read_window(layer: str, bbox):
    """Read a layer for bbox (lon_min, lat_min, lon_max, lat_max).

    Returns (array, transform). bbox must lie within a single 10° tile.
    """
    lon_c = (bbox[0] + bbox[2]) / 2
    lat_c = (bbox[1] + bbox[3]) / 2
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        with rasterio.open("/vsicurl/" + url(layer, lon_c, lat_c)) as src:
            win = from_bounds(*bbox, transform=src.transform).round_offsets().round_lengths()
            arr = src.read(1, window=win)
            transform = src.window_transform(win)
    return arr, transform


def post_cutoff_loss(lossyear: np.ndarray) -> np.ndarray:
    """Boolean mask of loss after the EUDR cut-off (2021+)."""
    return lossyear > (EUDR_CUTOFF_YEAR - 2000)
