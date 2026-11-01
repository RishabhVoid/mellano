"""One geographic contract for preprocessing, simulation and web export."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
DERIVED = ROOT / "data" / "derived"
RUNS = ROOT / "runs"
WEB_DATA = ROOT / "web" / "public" / "data"
AOI = (77.20, 28.58, 77.32, 28.72)  # west, south, east, north; EPSG:4326
CRS = "EPSG:32643"
CELL_SIZE = 10.0
DEM_TILE = "Copernicus_DSM_COG_10_N28_00_E077_00_DEM"
DEM_URL = f"https://copernicus-dem-30m.s3.amazonaws.com/{DEM_TILE}/{DEM_TILE}.tif"
FABDEM_TILE = "N28E077_FABDEM_V1-2.tif"
FABDEM_ARCHIVE_URL = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/N20E070-N30E080_FABDEM_V1-2.zip"
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter")
MASK_NAMES = ("s1_water_before", "s1_water_peak", "s1_flood_new", "s1_water_recession")
SCENARIOS = ("normal", "replay2023", "breach_near_ito", "debris_ring_road", "river_plus_1m", "relative_river_plus_1m", "relative_river_plus_2m", "relative_river_plus_3m", "relative_breach_near_ito", "relative_debris_ring_road")
