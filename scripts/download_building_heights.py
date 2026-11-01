"""Public AOI download. Run from the repo root with PYTHONPATH=. uv run python scripts/download_building_heights.py."""
import json
from pathlib import Path
import numpy as np
import requests
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from sim.config import RAW, DERIVED, CRS
from sim.data import write_json
from sim.http_range import range_opener

if (DERIVED / 'open_buildings_height_2023.tif').is_file():
    print('2023 AOI heights already cached.', flush=True)
    raise SystemExit

manifest_url = 'https://storage.googleapis.com/open-buildings-temporal-data/v1/manifests/39_EPSG_32643_2023_06_30.json'
path = RAW / 'open_buildings_height_manifest_2023.json'
if not path.is_file():
    response = requests.get(manifest_url, timeout=120)
    response.raise_for_status()
    write_json(path, response.json())
manifest = json.loads(path.read_text())
with rasterio.open(DERIVED / 'ground_10m.tif') as ground:
    bounds = ground.bounds
    width, height = ground.width * 5, ground.height * 5
affine = from_origin(bounds.left, bounds.top, 2, 2)
values = np.full((height, width), -9999, dtype='float32')
sources = []
for source in manifest['tilesets'][0]['sources']:
    a, d = source['affineTransform'], source['dimensions']
    x, y = a['translateX'], a['translateY']
    ex, sy = x + d['width'] * a['scaleX'], y + d['height'] * a['scaleY']
    if x < bounds.right and ex > bounds.left and sy < bounds.top and y > bounds.bottom:
        sources.append((manifest['uriPrefix'] + source['uris'][0]).replace('gs://', 'https://storage.googleapis.com/'))
with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_MULTIRANGE='YES', GDAL_HTTP_MAX_RETRY=3, GDAL_HTTP_TIMEOUT=120, GDAL_CACHEMAX=256*1024*1024):
    for index, url in enumerate(sources):
        print(f'Height raster window {index+1}/{len(sources)}: {url}', flush=True)
        cached = RAW / ('ob2023_' + url.rsplit('/', 1)[-1])
        with rasterio.open(cached if cached.is_file() else url.rsplit("/",1)[-1], **({} if cached.is_file() else {"opener": range_opener(url)})) as source:
            left, right = max(bounds.left, source.bounds.left), min(bounds.right, source.bounds.right)
            bottom, top = max(bounds.bottom, source.bounds.bottom), min(bounds.top, source.bounds.top)
            rows, cols = round((top-bottom)/2), round((right-left)/2)
            window = rasterio.windows.from_bounds(left, bottom, right, top, source.transform)
            data = source.read([2, 3], window=window, out_shape=(2, rows, cols), masked=True, resampling=Resampling.nearest)
            heights, presence = data[0], data[1]
            valid = ~np.ma.getmaskarray(heights) & ~np.ma.getmaskarray(presence) & (presence.data >= .5) & (heights.data > 0) & (heights.data <= 100)
            row, col = round((bounds.top-top)/2), round((left-bounds.left)/2)
            target = values[row:row+rows,col:col+cols]
            target[valid] = heights.data[valid]
        print(f'Valid height pixels accumulated: {int((values>0).sum()):,}', flush=True)
output = DERIVED / 'open_buildings_height_2023.tif'
temporary = output.with_suffix('.tmp.tif')
with rasterio.open(temporary, 'w', driver='GTiff', width=width, height=height, count=1, dtype='float32', crs=CRS, transform=affine, nodata=-9999, tiled=True, compress='deflate') as destination:
    destination.write(values, 1)
    destination.update_tags(source='GOOGLE/Research/open-buildings-temporal/v1', year='2023', band='building_height', building_presence_min='0.5', sampling='2 m nearest; native files 0.5 m; effective resolution 4 m')
temporary.replace(output)
write_json(DERIVED / 'open_buildings_height_2023_source.json', {'dataset': 'GOOGLE/Research/open-buildings-temporal/v1', 'year': 2023, 'band': 'building_height', 'manifest_url': manifest_url, 'tile_urls': sources, 'properties': manifest['properties'], 'sampling_m': 2, 'resampling': 'nearest', 'effective_resolution_m': 4, 'building_presence_min': .5, 'valid_pixels': int((values>0).sum()), 'license': 'ODbL'})
print('Real 2023 height raster ready.', flush=True)
