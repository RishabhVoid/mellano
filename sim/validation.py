"""Geometric water-extent overlap; never a forecast-validation claim."""
from functools import lru_cache
import hashlib

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling

from sim.config import DERIVED
from sim.replay import overlap

CAVEAT = ('Geometric overlap with an OSM-tuned Sentinel-1 classification, not independent ground truth. '
          'Acquisition and simulation times may differ. Hypothetical scenarios are not validated forecasts.')


@lru_cache(maxsize=2)
def _aligned_mask(path, size, mtime, shape, transform, crs):
    with rasterio.open(path) as source:
        if source.crs is None: raise ValueError('Satellite mask CRS is missing')
        values = source.read(1, masked=True).astype('float32').filled(np.nan)
        values[~np.isin(values, [0, 1])] = np.nan
        aligned = np.full(shape, np.nan, dtype='float32')
        reproject(values, aligned, src_transform=source.transform, src_crs=source.crs,
                  src_nodata=np.nan, dst_transform=transform, dst_crs=crs,
                  dst_nodata=np.nan, resampling=Resampling.nearest, num_threads=1)
        tags = source.tags()
    sha = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): sha.update(chunk)
    return aligned, {'mask_sha256': sha.hexdigest(), 'acquisition_date': tags.get('acquisition_date'),
                     'source_product': tags.get('source_product')}


def water_overlap(depth, valid, profile, mask_path=None):
    result = {'iou_vs_sentinel_peak': None, 'sentinel_comparison': {'available': False, 'caveat': CAVEAT}}
    note = result['sentinel_comparison']
    if depth is None:
        note['reason'] = 'This module has no computed water depth'; return result
    path = mask_path or DERIVED / 's1_water_peak.tif'
    if any(p.is_symlink() for p in (path, *path.parents)):
        note['reason'] = 'Satellite mask must be a regular source file'; return result
    if not path.is_file():
        note['reason'] = 'Real Sentinel-1 peak classification unavailable'; return result
    stat = path.stat()
    observations, metadata = _aligned_mask(str(path), stat.st_size, stat.st_mtime_ns, depth.shape, profile['transform'], str(profile['crs']))
    observed = np.isfinite(observations) & valid & np.isfinite(depth)
    cell_area = abs(profile['transform'].a * profile['transform'].e - profile['transform'].b * profile['transform'].d)
    note.update(metadata, evaluated_area_km2=float(observed.sum()*cell_area/1e6), evaluated_cells=int(observed.sum()),
                mask_file='data/derived/s1_water_peak.tif', definition='Computed depth >0.1m versus classified water, restricted to common valid pixels; excludes SAR NoData.')
    if not observed.any():
        note['reason'] = 'No valid satellite pixels overlap this run'; return result
    result['iou_vs_sentinel_peak'] = overlap(depth > .1, observations == 1, observed)['iou']
    note.update(available=result['iou_vs_sentinel_peak'] is not None,
                reason=None if result['iou_vs_sentinel_peak'] is not None else 'Both evaluated water extents are empty; IoU undefined')
    return result
