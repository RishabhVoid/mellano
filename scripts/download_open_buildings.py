"""Stream the official v3 source tile; keep only AOI records, then atomically publish. Run with PYTHONPATH=. uv run python scripts/download_open_buildings.py."""
import csv
import gzip
import io
import json
import time
from pathlib import Path
import requests

from sim.config import AOI, RAW
from sim.data import write_json

url = 'https://storage.googleapis.com/open-buildings-data/v3/polygons_s2_level_4_gzip/391_buildings.csv.gz'
output = RAW / 'open_buildings_v3_aoi.csv'
if output.is_file():
    print('AOI footprints already cached.', flush=True)
    raise SystemExit
west, south, east, north = AOI
started = time.monotonic()
with requests.get(url, stream=True, timeout=(30, 180)) as response:
    response.raise_for_status()
    reader = csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=response.raw)))
    count = 0
    with output.with_suffix('.part').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=reader.fieldnames)
        writer.writeheader()
        for n, row in enumerate(reader):
            lon, lat = float(row['longitude']), float(row['latitude'])
            if west - .002 < lon < east + .002 and south - .002 < lat < north + .002:
                writer.writerow(row)
                count += 1
            if n and n % 1000000 == 0:
                print(f'{n:,} source rows scanned; {count:,} AOI candidates; {time.monotonic()-started:.0f}s', flush=True)
    output.with_suffix('.part').replace(output)
write_json(RAW / 'open_buildings_v3_source.json', {'url': url, 'version': 3, 'aoi_candidates': count, 'source_rows': n+1, 'license': 'ODbL', 'spatial_filter_margin_degrees': .002})
print(f'Finished: {count:,} candidates', flush=True)
