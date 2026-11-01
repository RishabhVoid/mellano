"""Real Sentinel-1 water classification, tuned on OSM before the flood only.

PC RTC is preferred; Earth Engine GRD_FLOAT is an explicitly authenticated
fallback for every period together, so sigma0 and gamma0 never share a threshold.
No classified mask is emitted without genuine data in the exact before window.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import subprocess
import time

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling
from scipy.ndimage import binary_erosion, median_filter
from pyproj import Transformer
import requests
from shapely.geometry import shape
from sim.config import AOI, DERIVED, RAW
from sim.data import write_json
from sim.geometry import transform_xy
from sim.jobs import active, launch, state, update

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
SIGN = 'https://planetarycomputer.microsoft.com/api/sas/v1/sign'
WINDOWS = {'before': ('2023-07-01', '2023-07-07'), 'peak': ('2023-07-12', '2023-07-19'), 'recession': ('2023-07-20', '2023-07-27')}
CACHE = RAW / 'sentinel1'
CLASSIFICATION = 'OSM-tuned Sentinel-1 VV threshold classification; not independent ground truth'


def note(message, status='running'):
    print(f'[{datetime.now(timezone.utc).isoformat(timespec="seconds")}] {message}', flush=True)
    update('job_a', message, status=status)


def session():
    client = requests.Session()
    client.trust_env = False  # No credential, proxy or environment-file discovery.
    client.headers['User-Agent'] = 'Otec real Sentinel-1 AOI analysis'
    return client


def get_json(client, url, **kwargs):
    for attempt in range(3):
        try:
            response = client.get(url, timeout=(20, 90), **kwargs)
            if response.status_code in (401, 403, 404):
                raise PermissionError(f'Public source returned HTTP {response.status_code}')
            response.raise_for_status()
            return response.json()
        except PermissionError:
            raise
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise RuntimeError('Public catalog request failed after three attempts') from None
            time.sleep(2 * (attempt + 1))


def discover(client):
    catalogs = {}
    for collection in ('sentinel-1-rtc', 'sentinel-1-grd'):
        catalogs[collection] = {}
        for period, (start, end) in WINDOWS.items():
            # Inclusive requested calendar dates, represented by exclusive next day.
            end_inclusive = str(np.datetime64(end) - np.timedelta64(1, 's')) + 'Z'
            data = get_json(client, STAC + '/search', params={'collections': collection, 'bbox': ','.join(map(str, AOI)), 'datetime': start + 'T00:00:00Z/' + end_inclusive, 'limit': 100})
            items = [item for item in data['features'] if 'vv' in item['assets']]
            catalogs[collection][period] = items
            note(f'{collection}: {period} has {len(items)} VV scene(s) in the exact requested window')
    CACHE.mkdir(parents=True, exist_ok=True)
    write_json(CACHE / 'catalog.json', {'queried_at': datetime.now(timezone.utc).isoformat(), 'windows_end_exclusive': WINDOWS, 'collections': catalogs})
    return catalogs


def save_raster(path, values, profile, nodata, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.part.tif')
    options = {**profile, 'driver': 'GTiff', 'count': 1, 'dtype': values.dtype, 'nodata': nodata, 'compress': 'deflate', 'tiled': True}
    with rasterio.open(temporary, 'w', **options) as output:
        output.write(values, 1)
        output.update_tags(**{key: str(value) for key, value in tags.items()})
    temporary.replace(path)


def clip_rtc(client, item, profile):
    identifier = item['id']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
        raise ValueError('Unexpected source identifier')
    output = CACHE / (identifier + '.linear_10m.tif')
    if output.is_file():
        with rasterio.open(output) as cached:
            if cached.shape == (profile['height'], profile['width']) and cached.transform == profile['transform'] and cached.crs == profile['crs']:
                note(f'Reusing verified aligned clip {identifier}')
                return output
        raise ValueError('Cached Sentinel clip does not match current grid')
    href = item['assets']['vv']['href']
    signed = get_json(client, SIGN, params={'href': href})['href']
    note(f'Reading only the AOI from {identifier}')
    # Credentials stay in memory; exceptions are sanitized by main, never logged.
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.tiff', GDAL_HTTP_CONNECTTIMEOUT=20, GDAL_HTTP_TIMEOUT=90, GDAL_HTTP_MAX_RETRY=3, GDAL_HTTP_RETRY_DELAY=2):
        with rasterio.open(signed) as source:
            with WarpedVRT(source, crs=profile['crs'], transform=profile['transform'], width=profile['width'], height=profile['height'], nodata=-9999, resampling=Resampling.bilinear) as warped:
                values = warped.read(1, masked=True).filled(-9999).astype('float32')
    values[~np.isfinite(values) | (values <= 0)] = -9999
    save_raster(output, values, profile, -9999, source_id=identifier, acquisition_date=item['properties']['datetime'], product='PC RTC gamma0 linear', source_url=href)
    note(f'Completed AOI clip {identifier}; valid fraction {(values > 0).mean():.6f}')
    return output


def linear_to_filtered_db(linear, cell_size=10., radius_m=30.):
    valid = np.isfinite(linear) & (linear > 0)
    db = np.zeros(linear.shape, dtype='float32')
    db[valid] = 10 * np.log10(linear[valid])
    radius = int(np.ceil(radius_m / cell_size))
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    footprint = (xx * cell_size)**2 + (yy * cell_size)**2 <= radius_m**2
    filtered = median_filter(db, footprint=footprint, mode='nearest')
    # Missing SAR pixels never become dry land or synthetic reflected values.
    valid = binary_erosion(valid, structure=footprint, border_value=1)
    filtered[~valid] = np.nan
    return filtered.astype('float32')


def tune_threshold(before_db, river, valid):
    valid = valid & np.isfinite(before_db)
    if not np.any(river & valid):
        raise ValueError('No mapped OSM river cells have valid before-window SAR')
    curve = []
    for threshold in np.arange(-30., -4.999, .25):
        wet = (before_db < threshold) & valid
        intersection = int(np.count_nonzero(wet & river))
        union = int(np.count_nonzero((wet | river) & valid))
        curve.append({'threshold_db': float(threshold), 'iou': intersection / union if union else 0., 'intersection_cells': intersection, 'union_cells': union})
    # Stable ascending order chooses the lower/wetter-area-conservative threshold on ties.
    best = max(curve, key=lambda entry: entry['iou'])
    return best, curve


def river_mask(profile):
    path = DERIVED / 'osm_river.geojson'
    source = json.loads(path.read_text())
    project = Transformer.from_crs('EPSG:4326', profile['crs'], always_xy=True).transform
    polygons = [transform_xy(project, shape(feature['geometry'])) for feature in source['features'] if feature['geometry']['type'] in ('Polygon', 'MultiPolygon')]
    if not polygons:
        raise ValueError('Mapped OSM river polygons are missing')
    result = rasterize([(polygon, 1) for polygon in polygons], out_shape=(profile['height'], profile['width']), transform=profile['transform'], dtype='uint8').astype(bool)
    provenance = json.loads((DERIVED / 'provenance.json').read_text()) if (DERIVED / 'provenance.json').is_file() else {}
    return result, {'snapshot_time': provenance.get('osm', {}).get('timestamp'), 'file': str(path.relative_to(DERIVED.parent.parent)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'polygon_count': len(polygons), 'rasterization': 'pixel centres', 'license': 'ODbL', 'attribution': 'OpenStreetMap contributors', 'source_url': 'https://www.openstreetmap.org/copyright'}


def select_scene(candidates, preferred_orbit=None):
    if not candidates:
        return None
    matching = [candidate for candidate in candidates if preferred_orbit is not None and candidate['orbit'] == preferred_orbit and candidate['coverage_fraction'] >= .95]
    pool = matching or candidates
    return max(pool, key=lambda candidate: (candidate['coverage_fraction'], candidate['time']))


def classify(candidates, profile, source_product):
    with rasterio.open(DERIVED / 'aoi_mask.tif') as source:
        aoi = source.read(1).astype(bool)
    before = select_scene(candidates['before'])
    if before is None:
        raise ValueError('Exact before window has no source scene')
    chosen = {'before': before, 'peak': select_scene(candidates['peak'], before['orbit']), 'recession': select_scene(candidates['recession'], before['orbit'])}
    if any(value is None for value in chosen.values()):
        raise ValueError('A requested window has no source scene')
    filtered = {}
    for period, scene in chosen.items():
        note(f'Processing {period}: {scene["id"]}, orbit {scene["orbit"]}')
        with rasterio.open(scene['file']) as source:
            linear = source.read(1, masked=True).filled(np.nan)
        filtered[period] = linear_to_filtered_db(linear, profile['transform'].a)
        save_raster(CACHE / f'{period}_vv_filtered_db.tif', np.where(np.isfinite(filtered[period]), filtered[period], -9999).astype('float32'), profile, -9999, source_id=scene['id'], acquisition_date=scene['time'], units='dB')
    river, osm = river_mask(profile)
    best, curve = tune_threshold(filtered['before'], river, aoi)
    masks = {}
    for period in chosen:
        valid = aoi & np.isfinite(filtered[period])
        masks[period] = np.where(valid, filtered[period] < best['threshold_db'], 255).astype('uint8')
    common = aoi & np.isfinite(filtered['before']) & np.isfinite(filtered['peak'])
    masks['flood_new'] = np.where(common, (masks['peak'] == 1) & (masks['before'] == 0), 255).astype('uint8')
    old_meta = json.loads((DERIVED / 's1_meta.json').read_text()) if (DERIVED / 's1_meta.json').is_file() else {}
    meta = {key: value for key, value in old_meta.items() if key.startswith('pre_flood_')}
    meta.update({'status': 'complete', 'created_at': datetime.now(timezone.utc).isoformat(), 'classification': CLASSIFICATION, 'independent_ground_truth': False, 'source_product': source_product, 'source_catalog': STAC if source_product.startswith('PC') else 'https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD', 'windows_end_exclusive': WINDOWS, 'selected_scenes': chosen, 'available_scenes': candidates, 'threshold_db': best['threshold_db'], 'threshold_operator': 'VV_dB < threshold', 'before_osm_iou': best['iou'], 'threshold_search': {'minimum_db': -30, 'maximum_db': -5, 'step_db': .25, 'tie_break': 'lowest threshold', 'curve': curve}, 'focal_median_radius_m': 30, 'focal_median_shape': 'circle', 'focal_median_edge_mode': 'nearest; invalid input footprint eroded', 'osm_reference': osm, 'grid': {'crs': str(profile['crs']), 'transform': list(profile['transform'])[:6], 'width': profile['width'], 'height': profile['height'], 'cell_size_m': profile['transform'].a}, 'nodata': 255, 'warnings': ['Threshold is tuned against a contemporary OSM river outline, not independent July 2023 truth.', 'Dark SAR surfaces can include radar shadow and smooth non-water areas; urban/vegetated flood water can be missed.', 'The peak-window acquisition is not necessarily the July 13 gauge peak.']})
    for period, scene in chosen.items():
        meta[period + '_dates'] = [scene['time']]
        if scene['coverage_fraction'] < .95:
            meta['warnings'].append(f'{period} has incomplete SAR coverage ({scene["coverage_fraction"]:.4f}); nodata remains unknown.')
        if scene['orbit'] != before['orbit']:
            meta['warnings'].append(f'{period} orbit {scene["orbit"]} differs from before orbit {before["orbit"]}; viewing geometry limits threshold transfer.')
    meta['masks'] = {}
    for period, values in masks.items():
        name = 's1_flood_new' if period == 'flood_new' else 's1_water_' + period
        date = chosen['peak' if period == 'flood_new' else period]['time']
        save_raster(DERIVED / (name + '.tif'), values, profile, 255, acquisition_date=date, classification=CLASSIFICATION, threshold_db=best['threshold_db'], source_product=source_product)
        valid = values != 255
        meta['masks'][name] = {'path': name + '.tif', 'valid_cells': int(valid.sum()), 'water_cells': int((values == 1).sum()), 'water_area_km2': float((values == 1).sum() * profile['transform'].a**2 / 1e6), 'valid_aoi_fraction': float((valid & aoi).sum() / aoi.sum()), 'nodata_cells': int((~valid).sum()), 'sha256': hashlib.sha256((DERIVED / (name + '.tif')).read_bytes()).hexdigest()}
    write_json(DERIVED / 's1_meta.json', meta)
    note(f'Completed four genuine SAR masks; before OSM IoU {best["iou"]:.4f}, threshold {best["threshold_db"]:.2f} dB')
    return meta


def run_pc(profile):
    client = session()
    catalog = discover(client)
    candidates = {period: [] for period in WINDOWS}
    failed = []
    for period, items in catalog['sentinel-1-rtc'].items():
        for item in items:
            try:
                path = clip_rtc(client, item, profile)
                with rasterio.open(path) as source:
                    values = source.read(1, masked=True)
                    coverage = float(np.count_nonzero(~np.ma.getmaskarray(values) & (values.data > 0)) / values.size)
                props = item['properties']
                candidates[period].append({'id': item['id'], 'time': props['datetime'], 'orbit': props.get('sat:relative_orbit'), 'direction': props.get('sat:orbit_state'), 'file': str(path), 'coverage_fraction': coverage, 'source_url': item['assets']['vv']['href']})
            except Exception as error:
                failed.append({'id': item['id'], 'error_type': type(error).__name__})
                note(f'Could not read {item["id"]} ({type(error).__name__}); no substitute created')
    write_json(CACHE / 'processed_scenes.json', candidates)
    if not all(candidates.values()):
        meta = {'status': 'needs_input', 'classification': CLASSIFICATION, 'independent_ground_truth': False, 'windows_end_exclusive': WINDOWS, 'available_scenes': candidates, 'catalog_counts': {collection: {period: len(items) for period, items in periods.items()} for collection, periods in catalog.items()}, 'failed_reads': failed, 'reason': 'PC catalog lacks a usable acquisition in at least one exact requested window. Earth Engine fallback must use GRD_FLOAT consistently for all periods; no threshold or masks invented.', 'created_at': datetime.now(timezone.utc).isoformat()}
        for period in WINDOWS:
            meta[period + '_dates'] = [scene['time'] for scene in candidates[period]]
        write_json(DERIVED / 's1_meta.json', meta)
        note('Available PC RTC clips cached. Exact before window missing in PC; waiting for authorized Earth Engine login and project ID.', 'needs_input')
        return 2
    classify(candidates, profile, 'PC Sentinel-1 RTC gamma0 linear; CC BY 4.0')
    return 0



def gcloud_access_token(configuration, request=None, scopes=None):
    """Refresh from the explicitly verified CLI configuration; token stays in RAM."""
    if not re.fullmatch(r'[a-z][a-z0-9-]*', configuration):
        raise ValueError('Invalid gcloud configuration name')
    try:
        result = subprocess.run(['gcloud', '--configuration=' + configuration, 'auth', 'print-access-token', '--quiet'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Verified gcloud configuration token refresh unavailable') from None
    token = result.stdout.strip()
    if result.returncode or len(token) < 20 or any(character.isspace() for character in token):
        raise RuntimeError('Verified gcloud configuration token refresh unavailable')
    # google-auth uses naive UTC expiry; refresh before the usual one-hour lifetime.
    return token, datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=50)


def ee_credentials(ee, project, gcloud_configuration=None):
    if gcloud_configuration:
        from google.oauth2.credentials import Credentials
        class RefreshableGcloudCredentials(Credentials):
            def _make_copy(self):
                # google-auth quota/universe copies currently drop broker+expiry.
                copied = super()._make_copy()
                copied.refresh_handler = self.refresh_handler
                copied.expiry = self.expiry
                return copied
        return RefreshableGcloudCredentials(token=None, quota_project_id=project, refresh_handler=lambda request, scopes=None: gcloud_access_token(gcloud_configuration, request, scopes))
    credentials = ee.data.get_persistent_credentials()
    return credentials.with_quota_project(None) if hasattr(credentials, 'with_quota_project') else credentials


def run_ee(profile, project, gcloud_configuration=None):
    try:
        import ee
    except ImportError:
        note('Earth Engine dependency required: run this worker with uv run --with earthengine-api.', 'needs_input')
        return 2
    try:
        ee.Initialize(credentials=ee_credentials(ee, project, gcloud_configuration), project=project)
    except Exception:
        note('Earth Engine login/project initialization required; no credential details logged.', 'needs_input')
        return 2
    client = session()
    region = ee.Geometry.Rectangle(list(AOI), proj='EPSG:4326', geodesic=False)
    collection = ee.ImageCollection('COPERNICUS/S1_GRD_FLOAT').filterBounds(region).filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')).filter(ee.Filter.eq('instrumentMode', 'IW'))
    candidates = {period: [] for period in WINDOWS}
    preferred = None
    for period, (start, end) in WINDOWS.items():
        subset = collection.filterDate(start, end)
        count = subset.size().getInfo()
        if not count:
            note(f'Earth Engine has no image in exact {period} window; no window extension made.', 'needs_input')
            return 2
        ids = subset.aggregate_array('system:index').getInfo()
        images = []
        for identifier in ids:
            image = ee.Image('COPERNICUS/S1_GRD_FLOAT/' + identifier)
            props = image.toDictionary(['system:time_start', 'relativeOrbitNumber_start', 'orbitProperties_pass']).getInfo()
            images.append((image, {'id': identifier, 'time': datetime.fromtimestamp(props['system:time_start']/1000, timezone.utc).isoformat().replace('+00:00', 'Z'), 'orbit': props['relativeOrbitNumber_start'], 'direction': props['orbitProperties_pass'], 'source_url': 'https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD'}))
        # Before is earliest; for subsequent periods prefer its viewing geometry.
        matching = [item for item in images if item[1]['orbit'] == preferred]
        chosen_image, scene = min(matching or images, key=lambda item: item[1]['time'])
        if preferred is None:
            preferred = scene['orbit']
        output = CACHE / ('EE_' + scene['id'] + '.linear_10m.tif')
        if not output.is_file():
            note(f'Earth Engine export {period}: {scene["id"]}')
            link = chosen_image.select('VV').getDownloadURL({'name': 'otec_' + period, 'crs': str(profile['crs']), 'crs_transform': list(profile['transform'])[:6], 'dimensions': [profile['width'], profile['height']], 'format': 'GEO_TIFF'})
            response = client.get(link, timeout=(30, 240));response.raise_for_status()
            temporary = output.with_suffix('.download.tif');temporary.write_bytes(response.content)
            with rasterio.open(temporary) as raster:
                if raster.shape != (profile['height'], profile['width']) or raster.transform != profile['transform'] or raster.crs != profile['crs']:
                    raise ValueError('Earth Engine export alignment mismatch')
                values = raster.read(1, masked=True).filled(-9999).astype('float32')
            values[~np.isfinite(values) | (values <= 0)] = -9999
            save_raster(output, values, profile, -9999, source_id=scene['id'], acquisition_date=scene['time'], product='Earth Engine Sentinel-1 GRD_FLOAT sigma0 linear')
            temporary.unlink()
        with rasterio.open(output) as raster:
            values = raster.read(1, masked=True)
            scene['coverage_fraction'] = float(np.count_nonzero(~np.ma.getmaskarray(values) & (values.data > 0)) / values.size)
        scene['file'] = str(output)
        scene['window_available_ids'] = ids
        scene['window_available_scenes'] = [{key: details[key] for key in ('id', 'time', 'orbit', 'direction', 'source_url')} for _, details in images]
        candidates[period].append(scene)
    write_json(CACHE / 'earthengine_scenes.json', candidates)
    classify(candidates, profile, 'Earth Engine COPERNICUS/S1_GRD_FLOAT sigma0 linear')
    return 0



def project_access(project, gcloud_configuration=None):
    """Read-only probe; reload authorized credentials, never log exceptions/URLs."""
    try:
        import ee
        ee.Reset()
        credentials = ee_credentials(ee, project, gcloud_configuration)
        ee.Initialize(credentials=credentials, project=project)
        ee.data.setDeadline(30000)
        return ee.Number(1).getInfo() == 1, 'ready'
    except ImportError:
        return False, 'Earth Engine Python dependency unavailable'
    except Exception as error:
        # Only fixed strings leave this function: upstream errors can carry tokens.
        message = str(error).lower()
        if ('noncommercial' in message or 'non-commercial' in message) and 'expired' in message:
            reason = 'Earth Engine noncommercial eligibility expired; reverify this project in Earth Engine configuration'
        elif 'not registered' in message or 'register your project' in message:
            reason = 'Earth Engine project registration pending'
        elif 'serviceusage.services.use' in message:
            reason = 'Project serviceusage.services.use permission pending'
        elif 'disabled' in message or 'has not been used' in message:
            reason = 'Earth Engine API enablement pending'
        elif 'authenticate' in message or 'invalid_grant' in message:
            reason = 'Earth Engine login requires attention'
        elif 'permission' in message or 'forbidden' in message:
            reason = 'Earth Engine project access permission pending'
        else:
            reason = 'Earth Engine access unavailable; waiting to retry'
        return False, reason


def wait_project(project, gcloud_configuration=None):
    """Nohup gate: one read-only attempt per minute, then one guarded launch."""
    job = 'job_a_wait_project'
    attempt = 0
    while True:
        current = state('job_a') or {}
        if active('job_a') or current.get('status') == 'complete':
            update(job, 'Job A is already running or complete; no duplicate launched')
            return 0
        attempt += 1
        ready, reason = project_access(project, gcloud_configuration)
        if ready:
            command = ['uv', 'run', '--frozen', '--with', 'earthengine-api', 'python', '-u', '-m', 'sim.sentinel', 'run', '--ee-project', project]
            if gcloud_configuration:
                command += ['--gcloud-configuration', gcloud_configuration]
            result = launch('job_a', command, detail='Earth Engine project ready; acquiring real before/peak/recession scenes')
            outcome = 'launched' if result.get('launched') else 'already running or complete'
            update(job, f'Earth Engine ready; Job A {outcome}; gate finished', attempts=attempt)
            print(f'Earth Engine ready; Job A {outcome}.', flush=True)
            return 0
        update(job, reason + '; checking again in 60 seconds', status='running', attempts=attempt)
        print(f'Attempt {attempt}: {reason}. Next read-only check in 60 seconds.', flush=True)
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('step', choices=['run', 'wait-project'])
    parser.add_argument('--ee-project')
    parser.add_argument('--gcloud-configuration')
    args = parser.parse_args()
    if args.step == 'wait-project':
        if not args.ee_project:
            parser.error('wait-project requires --ee-project')
        return wait_project(args.ee_project, args.gcloud_configuration)
    CACHE.mkdir(parents=True, exist_ok=True)
    with rasterio.open(DERIVED / 'ground_10m.tif') as reference:
        profile = reference.profile.copy()
    try:
        return run_ee(profile, args.ee_project, args.gcloud_configuration) if args.ee_project else run_pc(profile)
    except Exception as error:
        # Raster/network exceptions can embed signed URLs: only log the type.
        note(f'Job A stopped safely ({type(error).__name__}); no placeholder masks emitted.', 'blocked')
        return 1


if __name__ == '__main__':
    sys.exit(main())
