"""Real mapped response context and count-conserving WorldPop AOI preparation."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import time

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.windows import Window, from_bounds
import requests
from scipy.spatial import cKDTree
import shapely
from shapely.geometry import LineString, Polygon, box

from .config import AOI, CRS, DERIVED, OVERPASS_URLS, RAW, WEB_DATA
from .jobs import update

KINDS = ('hospital', 'clinic', 'fire_station', 'police', 'school')
SPEEDS = {'motorway': 60., 'trunk': 50., 'primary': 40., 'secondary': 35.,
          'tertiary': 30., 'unclassified': 25., 'residential': 25.}
HIGHWAYS = set(SPEEDS) | {k + '_link' for k in SPEEDS}
POP_URL = 'https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/IND/ind_ppp_2020.tif'
POP_SOURCE = 'https://hub.worldpop.org/geodata/summary?id=6527'
JOB = 'response_context'
USER_AGENT = 'Otec/0.1 (Delhi flood-response research; AOI-only public data acquisition)'


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n')
    temp.replace(path)


def grid_contract():
    with rasterio.open(DERIVED / 'ground_10m.tif') as src:
        return {'width': src.width, 'height': src.height, 'transform': src.transform,
                'crs': src.crs, 'origin': [src.transform.c, src.transform.f],
                'profile': src.profile.copy()}


def local_point(lon, lat, origin, transform):
    x, y = transform.transform(lon, lat)
    return [round(x - origin[0], 3), round(origin[1] - y, 3)]


def speed(tags, direction):
    raw = tags.get('maxspeed:' + direction, tags.get('maxspeed', ''))
    numbers = re.findall(r'(?<![\w.])(\d+(?:\.\d+)?)', raw)
    if numbers:
        value = min(float(x) for x in numbers) * (1.609344 if 'mph' in raw.lower() else 1.)
        if 0 < value <= 150:
            return value, 'OSM maxspeed' + (' (mph converted)' if 'mph' in raw.lower() else '')
    kind = tags['highway']
    return min(SPEEDS[kind.removesuffix('_link')], 25.) if kind.endswith('_link') else SPEEDS[kind], 'assumed free-flow speed by highway class'


def directions(tags):
    value = tags.get('oneway', '').lower()
    if value in ('reversible', 'alternating'):
        return []
    if value == '-1':
        return [-1]
    if value in ('yes', '1', 'true'):
        return [1]
    if value in ('no', '0', 'false'):
        return [1, -1]
    return [1] if tags.get('junction') == 'roundabout' or tags['highway'] in ('motorway', 'motorway_link') else [1, -1]


def build_graph(elements, origin, bounds, transformer=None):
    """Connect shared OSM IDs only; planar crossings never create junctions."""
    transformer = transformer or Transformer.from_crs('EPSG:4326', CRS, always_xy=True)
    clip = box(*bounds)
    nodes, edges = {}, []
    skipped = Counter()
    for way in elements:
        tags = way.get('tags', {})
        if way.get('type') != 'way' or tags.get('highway') not in HIGHWAYS:
            continue
        ids, geometry = way.get('nodes', []), way.get('geometry', [])
        if len(ids) != len(geometry) or len(ids) < 2:
            skipped['incomplete_geometry_ways'] += 1
            continue
        if not directions(tags):
            skipped['reversible_or_alternating_ways'] += 1
            continue
        for i, (a, b) in enumerate(zip(geometry, geometry[1:])):
            if not a or not b:
                skipped['missing_coordinates_segments'] += 1
                continue
            points = [local_point(p['lon'], p['lat'], origin, transformer) for p in (a, b)]
            line = LineString(points)
            clipped = line.intersection(clip)
            if clipped.is_empty or clipped.geom_type != 'LineString' or clipped.length < .01:
                continue
            xy = [list(p) for p in clipped.coords]
            endpoints = []
            for side, point in enumerate((xy[0], xy[-1])):
                original = points[side]
                is_original = math.dist(point, original) < .005
                node_id = f'osm:{ids[i + side]}' if is_original else f'boundary:{way["id"]}:{i}:{side}'
                nodes[node_id] = {'id': node_id, 'position': point, 'osm_node_id': ids[i + side] if is_original else None,
                                  'boundary': not is_original}
                endpoints.append(node_id)
            access = tags.get('motor_vehicle', tags.get('vehicle', tags.get('access', 'yes')))
            allowed = access not in ('no', 'private')
            for sign in directions(tags):
                velocity, speed_source = speed(tags, 'forward' if sign == 1 else 'backward')
                edges.append({'id': f'way:{way["id"]}:{i}:{"f" if sign == 1 else "r"}',
                    'from': endpoints[0 if sign == 1 else 1], 'to': endpoints[1 if sign == 1 else 0],
                    'way_id': way['id'], 'geometry': xy if sign == 1 else xy[::-1],
                    'length_m': round(clipped.length, 3), 'speed_kmh': velocity, 'speed_source': speed_source,
                    'travel_time_s': round(clipped.length / (velocity / 3.6), 6), 'highway': tags['highway'],
                    'name': tags.get('name'), 'bridge': tags.get('bridge', 'no'), 'tunnel': tags.get('tunnel', 'no'),
                    'layer': tags.get('layer', '0'), 'oneway': tags.get('oneway'),
                    'maxspeed': tags.get('maxspeed'), 'access': access, 'routing_allowed': allowed})
    parents = {key: key for key in nodes}
    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    for edge in edges:
        if edge['routing_allowed']:
            parents[find(edge['from'])] = find(edge['to'])
    components = Counter(find(key) for key in nodes)
    return {'schema_version': 1, 'coordinate_system': 'local_metres_east_south', 'origin_utm': origin,
        'crs': CRS, 'nodes': list(nodes.values()), 'edges': edges,
        'meta': {'source': 'OpenStreetMap contributors', 'license': 'ODbL', 'source_url': 'https://www.openstreetmap.org/copyright',
        'node_count': len(nodes), 'directed_edge_count': len(edges), 'weak_component_count': len(components),
        'largest_weak_component_nodes': max(components.values(), default=0), 'skipped': dict(skipped),
        'topology': 'Shared OSM node IDs only. No planar intersections or snapped bridge crossings. AOI boundary clips are unique per road segment.',
        'limitations': 'OSM completeness and current access unknown. Turn restrictions and conditional restrictions are not implemented. AOI-truncated routes cannot detour outside the grid. No inferred bridge deck elevations.',
        'fallback_speeds_kmh': SPEEDS, 'link_speed_rule': 'min(parent class speed,25 km/h)',
        'speed_assumptions': 'Fallback speeds are modelling assumptions, not observed travel speeds. No live traffic; no emergency one-way override.'}}


def fetch_facilities():
    path = RAW / 'osm_response_facilities.json'
    if path.is_file():
        return json.loads(path.read_text()), 'complete_cached'
    west, south, east, north = AOI
    query = f'[out:json][timeout:25];nwr["amenity"~"^({"|".join(KINDS)})$"]({south},{west},{north},{east});out center;'
    session = requests.Session(); session.trust_env = False
    session.headers['User-Agent'] = USER_AGENT
    errors = []
    for url in OVERPASS_URLS:
        update(JOB, 'Fetching real OSM response facilities; road geometry reused from existing cache')
        try:
            response = session.get(url, params={'data': query}, timeout=(10, 40))
            response.raise_for_status()
            value = response.json()
            if 'elements' not in value or value.get('remark'):
                raise ValueError('Incomplete Overpass response')
            save_json(path, value)
            return value, 'complete_fetched'
        except (requests.RequestException, ValueError) as exc:
            code = getattr(getattr(exc, 'response', None), 'status_code', None)
            errors.append({'endpoint': url, 'error': type(exc).__name__, 'http_status': code})
    cached = json.loads((RAW / 'osm_aoi.json').read_text())
    return {'elements': [x for x in cached['elements'] if x.get('tags', {}).get('amenity') in KINDS],
            'osm3s': cached.get('osm3s'), 'fetch_errors': errors}, 'partial_cached_ways_only'


def build_stations(value, grid, graph, bbox=AOI):
    transform = Transformer.from_crs('EPSG:4326', grid.get('crs', CRS), always_xy=True)
    stations = []
    candidates = [n for n in graph['nodes'] if not n['boundary']]
    tree = cKDTree([n['position'] for n in candidates]) if candidates else None
    for item in value['elements']:
        tags = item.get('tags', {})
        kind = tags.get('amenity')
        if kind not in KINDS:
            continue
        centre = item if item['type'] == 'node' else item.get('center')
        location_method = 'OSM mapped point' if item['type'] == 'node' else 'OSM bounding-box centre (not an entrance)'
        if centre is None and item.get('geometry'):
            points = [(p['lon'], p['lat']) for p in item['geometry'] if p]
            if len(points) >= 3:
                point = Polygon(points).representative_point()
                centre = {'lon': point.x, 'lat': point.y}
                location_method = 'Mapped footprint interior point (not an entrance)'
        if not centre or not (bbox[0] <= centre['lon'] <= bbox[2] and bbox[1] <= centre['lat'] <= bbox[3]):
            continue
        position = local_point(centre['lon'], centre['lat'], grid['origin'], transform)
        distance, index = tree.query(position) if tree is not None else (None, None)
        stations.append({'id': f'{item["type"]}:{item["id"]}', 'osm_type': item['type'], 'osm_id': item['id'],
            'kind': kind, 'name': tags.get('name:en', tags.get('name')), 'position': position,
            'lon': centre['lon'], 'lat': centre['lat'], 'availability': 'unknown', 'capacity': None,
            'designated_relief_site': False if kind == 'school' else None,
            'role_label': 'Potential relief site' if kind == 'school' else kind.replace('_', ' '),
            'source': 'OpenStreetMap', 'location_method': location_method,
            'source_url': f'https://www.openstreetmap.org/{item["type"]}/{item["id"]}',
            'nearest_node_id': candidates[int(index)]['id'] if index is not None else None,
            'nearest_node_distance_m': round(float(distance), 2) if distance is not None else None,
            'road_connection_note': 'Nearest mapped road node is a routing assumption; facility entrance/access is not verified.'})
    return stations


class BoundedRangeFile(io.RawIOBase):
    """Reject full-country responses; cap total network payload, including TIFF metadata."""
    def __init__(self, url, session, budget=128 * 1024 * 1024):
        self.url, self.session, self.budget = url, session, budget
        self.position, self.downloaded, self.size = 0, 0, None
        self.cache = {}
        self._range(0, 16383)

    def _range(self, start, end):
        if self.downloaded + end - start + 1 > self.budget:
            raise OSError('WorldPop AOI HTTP range budget exceeded; full country download prohibited')
        with self.session.get(self.url, headers={'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'},
                              stream=True, timeout=(15, 45)) as response:
            if response.status_code != 206:
                raise OSError(f'WorldPop server did not honor HTTP Range (HTTP {response.status_code}); full country download refused')
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not match or int(match[1]) != start:
                raise OSError('Invalid WorldPop Content-Range')
            self.size = int(match[3])
            data = response.raw.read(end - start + 2)
            if len(data) != int(match[2]) - start + 1:
                raise OSError('Incomplete WorldPop range')
            self.downloaded += len(data)
            self.cache[start] = data
            return data

    def read(self, size=-1):
        if size < 0:
            size = self.size - self.position
        if size > self.budget:
            raise OSError('Full WorldPop raster read prohibited')
        end = min(self.position + size, self.size)
        chunks = []
        while self.position < end:
            start = self.position // 65536 * 65536
            data = self.cache.get(start)
            if data is None or self.position >= start + len(data):
                data = self._range(start, min(start + 65535, self.size - 1))
            count = min(end - self.position, len(data) - (self.position - start))
            chunks.append(data[self.position - start:self.position - start + count])
            self.position += count
        return b''.join(chunks)

    def seek(self, offset, whence=0):
        self.position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        return self.position

    def tell(self):
        return self.position

    def readable(self):
        return True

    def seekable(self):
        return True


def conservative_population(values, source_transform, source_crs, destination_transform, shape):
    """Allocate each source count by exact projected polygon overlap, never replicate counts."""
    height, width = shape
    result, covered = np.zeros(shape, np.float64), np.zeros(shape, np.float64)
    project = Transformer.from_crs(source_crs, CRS, always_xy=True)
    x0, y0, cell = destination_transform.c, destination_transform.f, destination_transform.a
    source_valid, allocated, input_total = 0, 0., 0.
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            value = values[row, col]
            if np.ma.is_masked(value) or not np.isfinite(value) or value < 0:
                continue
            source_valid += 1; input_total += float(value)
            corners = [source_transform * p for p in ((col, row), (col + 1, row), (col + 1, row + 1), (col, row + 1))]
            xx, yy = project.transform(*zip(*corners))
            polygon = Polygon(zip(xx, yy))
            if polygon.area <= 0:
                continue
            left, bottom, right, top = polygon.bounds
            c0, c1 = max(0, math.floor((left-x0)/cell)), min(width, math.ceil((right-x0)/cell))
            r0, r1 = max(0, math.floor((y0-top)/cell)), min(height, math.ceil((y0-bottom)/cell))
            if c0 >= c1 or r0 >= r1:
                continue
            rr, cc = np.meshgrid(np.arange(r0, r1), np.arange(c0, c1), indexing='ij')
            cells = shapely.box(x0+cc*cell, y0-(rr+1)*cell, x0+(cc+1)*cell, y0-rr*cell)
            area = shapely.area(shapely.intersection(cells, polygon))
            counts = float(value) * area / polygon.area
            result[r0:r1, c0:c1] += counts
            covered[r0:r1, c0:c1] += area
            allocated += float(counts.sum())
    full = covered >= cell * cell * .99999
    incomplete_count = float(result[~full].sum())
    result[~full] = np.nan
    return result.astype('float32'), {'source_valid_pixel_count': source_valid,
        'source_window_population': input_total, 'allocated_population_before_coverage_mask': allocated,
        'partial_coverage_population_excluded': incomplete_count,
        'expected_valid_population': allocated - incomplete_count,
        'valid_cell_count': int(full.sum()), 'nodata_cell_count': int((~full).sum()),
        'coverage_fraction': float(full.mean()), 'valid_population_total': float(np.nansum(result)),
        'method': 'Source pixel count multiplied by UTM polygon intersection area/source pixel area; contributions summed into exact 10m cells. Incomplete source coverage stays NoData. Uniform density inside each native pixel is an explicit assumption.'}


def prepare_population(grid):
    target, meta_path = DERIVED / 'population_10m.tif', DERIVED / 'population_meta.json'
    if target.is_file() and meta_path.is_file():
        return json.loads(meta_path.read_text())
    meta = {'available': False, 'status': 'missing', 'source': 'WorldPop', 'year': 2020,
        'source_url': POP_SOURCE, 'download_url': POP_URL, 'license': 'CC BY 4.0',
        'license_url': 'https://hub.worldpop.org/data/licence.txt', 'doi': '10.5258/SOTON/WP00645',
        'native_resolution_m': 100, 'native_resolution_description': '3 arcseconds, approximately 100m at equator',
        'unit': 'people per cell', 'output_cell_size_m': 10, 'crs': CRS,
        'origin_utm': grid['origin'], 'width': grid['width'], 'height': grid['height'],
        'coverage_fraction': None, 'valid_cell_count': None, 'nodata_cell_count': None,
        'valid_population_total': None, 'raster_path': None,
        'limitations': 'Modelled 2020 residential population, not current occupancy or observed evacuations. 10m allocation adds no demographic detail.',
        'checked_at': stamp()}
    source_path = RAW / 'worldpop_2020_aoi_native.tif'
    try:
        if not source_path.is_file():
            update(JOB, 'WorldPop: acquiring AOI via bounded HTTP ranges only; no India-wide download')
            session = requests.Session(); session.trust_env = False
            session.headers['User-Agent'] = USER_AGENT
            reader = BoundedRangeFile(POP_URL, session)
            def opener(path, mode='rb'):
                if path != 'worldpop.tif':
                    raise FileNotFoundError(path)
                reader.seek(0)
                return reader
            with rasterio.open('worldpop.tif', opener=opener) as src:
                projected = Transformer.from_crs(CRS, src.crs, always_xy=True)
                x0, y0 = grid['origin']; x1, y1 = x0 + grid['width']*10, y0-grid['height']*10
                xs, ys = projected.transform([x0,x1,x1,x0], [y0,y0,y1,y1])
                window = from_bounds(min(xs), min(ys), max(xs), max(ys), src.transform)
                window = Window(math.floor(window.col_off)-1, math.floor(window.row_off)-1,
                                math.ceil(window.width)+3, math.ceil(window.height)+3)
                values = src.read(1, window=window, masked=True)
                profile = src.profile.copy(); profile.update(width=values.shape[1], height=values.shape[0], transform=src.window_transform(window), compress='deflate')
                with rasterio.open(source_path, 'w', **profile) as dst:
                    dst.write(values.filled(src.nodata), 1)
                meta['download_bytes'] = reader.downloaded
        update(JOB, 'WorldPop: allocating native population counts by exact source/destination polygon overlap')
        with rasterio.open(source_path) as src:
            population, stats = conservative_population(src.read(1, masked=True), src.transform, src.crs,
                grid['transform'], (grid['height'], grid['width']))
        profile = grid['profile'].copy(); profile.update(dtype='float32', nodata=-9999., count=1)
        with rasterio.open(target, 'w', **profile) as dst:
            dst.write(np.where(np.isfinite(population), population, -9999.).astype('float32'), 1)
        meta.update(stats, available=True, status='available', raster_path='data/derived/population_10m.tif',
                    conservation_error_float32=float(np.nansum(population.astype('float64'))) - stats['expected_valid_population'])
    except (OSError, requests.RequestException, ValueError) as exc:
        meta['reason'] = str(exc)[:350]
    save_json(meta_path, meta)
    return meta


def download_population():
    """User-authorized whole-India fallback, one bounded-memory 2 MiB/s worker."""
    job = 'response_population'
    output = RAW / 'worldpop_2020_india.tif'
    partial = RAW / 'worldpop_2020_india.tif.part'
    provenance = RAW / 'worldpop_2020_india.json'
    grid = grid_contract()
    started = time.monotonic()
    context_path = DERIVED / 'response_context.json'
    def unavailable(status, reason):
        if not context_path.is_file():
            return
        context = json.loads(context_path.read_text())
        if not context.get('population', {}).get('available'):
            context['population'].update(status=status, job=job, reason=reason)
            save_json(DERIVED / 'population_meta.json', context['population'])
            for directory in (DERIVED, WEB_DATA):
                save_json(directory / 'response_context.json', context)
    if not output.is_file():
        unavailable('downloading', 'Authorized official full-country background download after range-crop failure; population values remain unavailable until clipping and count-conserving allocation complete.')
    if not output.is_file():
        free = shutil.disk_usage(RAW).free
        if free < 5 * 1024**3:
            unavailable('blocked', 'Insufficient disk headroom; at least5GiB free is required for the authorized download.')
            update(job, 'WorldPop whole-country fallback requires at least 5 GiB free disk; no transfer started', status='blocked')
            return 2
        existing = partial.stat().st_size if partial.is_file() else 0
        session = requests.Session(); session.trust_env = False
        session.headers.update({'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
        headers = {'Range': f'bytes={existing}-'} if existing else {}
        update(job, 'Downloading official WorldPop India2020 100m; 2 MiB/s cap, bounded memory', status='running')
        try:
            with session.get(POP_URL, headers=headers, stream=True, timeout=(20, 60)) as response:
                response.raise_for_status()
                if existing:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if response.status_code != 206 or not match or int(match[1]) != existing:
                        unavailable('blocked', 'Partial download preserved. Server does not support byte-range resume; no duplicate full transfer started.')
                        update(job, 'Partial WorldPop download preserved. Server refuses byte-range resume; no duplicate full transfer started.', status='blocked')
                        return 2
                    expected = int(match[3])
                else:
                    expected = int(response.headers.get('Content-Length', '0'))
                if expected and expected > free - 3 * 1024**3:
                    unavailable('blocked', 'Insufficient disk headroom for the server-reported file size.')
                    update(job, 'Insufficient disk headroom for WorldPop response; no body downloaded', status='blocked')
                    return 2
                digest = hashlib.sha256()
                if existing:
                    with partial.open('rb') as handle:
                        for chunk in iter(lambda: handle.read(1024*1024), b''):
                            digest.update(chunk)
                received, last_notice, transfer_start = existing, time.monotonic(), time.monotonic()
                with partial.open('ab' if existing else 'wb') as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk); digest.update(chunk); received += len(chunk)
                        pause = (received-existing)/(2*1024**2) - (time.monotonic()-transfer_start)
                        if pause > 0:
                            time.sleep(min(pause, 1.))
                        now = time.monotonic()
                        if now-last_notice >= 20:
                            detail = f'WorldPop download {received/1024**2:.1f}/{expected/1024**2:.1f} MiB; cap 2 MiB/s'
                            update(job, detail, downloaded_bytes=received, expected_bytes=expected)
                            print(detail, flush=True); last_notice = now
                if expected and received != expected:
                    raise OSError('WorldPop response length mismatch; incomplete .part preserved')
                with rasterio.open(partial) as source:
                    if source.count != 1 or source.crs.to_epsg() != 4326:
                        raise OSError('WorldPop raster metadata validation failed')
                partial.replace(output)
                save_json(provenance, {'source_url': POP_URL, 'catalog_url': POP_SOURCE,
                    'source': 'WorldPop India2020 unconstrained population counts', 'year': 2020,
                    'license': 'CC BY 4.0', 'doi': '10.5258/SOTON/WP00645', 'downloaded_at': stamp(),
                    'bytes': received, 'sha256': digest.hexdigest(), 'bandwidth_cap_bytes_per_second': 2*1024**2,
                    'method': 'Explicitly authorized full-country streamed fallback after official HTTP Range probes failed; local AOI clip only is used in the product.'})
        except (requests.RequestException, OSError, ValueError) as error:
            unavailable('missing', f'WorldPop download failed ({type(error).__name__}); partial bytes preserved, no population estimates published.')
            update(job, f'WorldPop download failed ({type(error).__name__}); partial data preserved, no raster or population estimate published', status='failed')
            return 2
    update(job, 'WorldPop downloaded; extracting exact AOI native pixels locally')
    native = RAW / 'worldpop_2020_aoi_native.tif'
    with rasterio.open(output) as src:
        project = Transformer.from_crs(CRS, src.crs, always_xy=True)
        x0, y0 = grid['origin']; x1, y1 = x0 + grid['width']*10, y0-grid['height']*10
        xs, ys = project.transform([x0,x1,x1,x0], [y0,y0,y1,y1])
        window = from_bounds(min(xs), min(ys), max(xs), max(ys), src.transform)
        window = Window(math.floor(window.col_off)-1, math.floor(window.row_off)-1,
                        math.ceil(window.width)+3, math.ceil(window.height)+3)
        values = src.read(1, window=window, masked=True)
        profile = src.profile.copy(); profile.update(width=values.shape[1], height=values.shape[0], transform=src.window_transform(window), compress='deflate')
        with rasterio.open(native, 'w', **profile) as dst:
            dst.write(values.filled(src.nodata), 1)
    update(job, 'WorldPop source clip complete; conserving counts into the exact10m simulation grid')
    # prepare_population uses this verified native cache, so performs no network request.
    population = prepare_population(grid)
    if not population.get('available'):
        unavailable('missing', 'Source downloaded, but aligned count allocation failed; no population estimates published.')
        update(job, 'WorldPop downloaded, but aligned allocation failed; no population estimate published', status='failed')
        return 2
    population['acquisition'] = json.loads(provenance.read_text()) if provenance.is_file() else {'file': output.name}
    save_json(DERIVED / 'population_meta.json', population)
    context = json.loads(context_path.read_text())
    context['population'] = population; context['generated_at'] = stamp()
    for directory in (DERIVED, WEB_DATA):
        save_json(directory / 'response_context.json', context)
    detail = f'WorldPop2020 available: {population["valid_cell_count"]} valid10m cells, {population["valid_population_total"]:.2f} modelled people; counts conserved'
    update(job, detail, status='complete', runtime_s=round(time.monotonic()-started,3),
           outputs=[str(DERIVED / 'population_10m.tif'), str(DERIVED / 'population_meta.json')])
    print(detail, flush=True)
    return 0


def clip_partial_population():
    """Read only complete native AOI blocks already present in the single active download."""
    job = 'response_population_clip'
    grid = grid_contract()
    partial = RAW / 'worldpop_2020_india.tif.part'
    native = RAW / 'worldpop_2020_aoi_native.tif'
    update(job, 'Decoding the bounded native AOI window from already-downloaded official TIFF bytes', status='running')
    snapshot_bytes = partial.stat().st_size
    with rasterio.open(partial) as src:
        if src.count != 1 or src.crs.to_epsg() != 4326:
            raise ValueError('Unexpected WorldPop source metadata')
        project = Transformer.from_crs(CRS, src.crs, always_xy=True)
        x0, y0 = grid['origin']; x1, y1 = x0+grid['width']*10, y0-grid['height']*10
        xs, ys = project.transform([x0,x1,x1,x0], [y0,y0,y1,y1])
        window = from_bounds(min(xs), min(ys), max(xs), max(ys), src.transform)
        window = Window(math.floor(window.col_off)-1, math.floor(window.row_off)-1,
                        math.ceil(window.width)+3, math.ceil(window.height)+3)
        # Rasterio raises on an incomplete compressed block. There is no missing-block fill.
        values = src.read(1, window=window, masked=True)
        if not values.count() or not np.isfinite(values.compressed()).all() or np.any(values.compressed() < 0):
            raise ValueError('WorldPop native window has no usable count data')
        profile = src.profile.copy()
        profile.update(width=values.shape[1], height=values.shape[0], transform=src.window_transform(window),
                       compress='deflate', tiled=True, blockxsize=256, blockysize=256)
        temporary = native.with_suffix('.clip.tif')
        with rasterio.open(temporary, 'w', **profile) as dst:
            dst.write(values.filled(src.nodata), 1)
        with rasterio.open(temporary) as check:
            decoded = check.read(1, masked=True)
            if not np.array_equal(decoded.data, values.data) or not np.array_equal(np.ma.getmaskarray(decoded), np.ma.getmaskarray(values)):
                raise ValueError('Native AOI clip verification mismatch')
        temporary.replace(native)
    verified = RAW / 'worldpop_2020_aoi_verified.tif'
    shutil.copyfile(native, verified)
    acquisition = {'source_url': POP_URL, 'catalog_url': POP_SOURCE, 'source': 'WorldPop India2020 unconstrained population counts',
        'year': 2020, 'license': 'CC BY 4.0', 'doi': '10.5258/SOTON/WP00645', 'clipped_at': stamp(),
        'method': 'All native AOI blocks decoded from already-downloaded official TIFF bytes; independently re-read lossless clip. Single full-country download continues separately.',
        'partial_file_snapshot_bytes': snapshot_bytes, 'full_country_sha256_pending': True,
        'native_clip_sha256': hashlib.sha256(verified.read_bytes()).hexdigest(),
        'native_clip_path': str(verified.relative_to(RAW.parents[1])),
        'native_window_shape': list(values.shape), 'native_valid_pixel_count': int(values.count()),
        'native_nodata_pixel_count': int(values.size-values.count())}
    save_json(RAW / 'worldpop_2020_aoi_native.json', acquisition)
    update(job, 'Native AOI blocks verified; conserving real2020 counts into exact10m cells')
    population = prepare_population(grid)
    if not population.get('available'):
        raise ValueError('Population allocation failed; no values published')
    population['acquisition'] = acquisition
    population['native_clip_sha256'] = acquisition['native_clip_sha256']
    population['native_clip_path'] = acquisition['native_clip_path']
    save_json(DERIVED / 'population_meta.json', population)
    context = json.loads((DERIVED / 'response_context.json').read_text())
    context['population'] = population; context['generated_at'] = stamp()
    for directory in (DERIVED, WEB_DATA):
        save_json(directory / 'response_context.json', context)
    detail = f'Real WorldPop2020 AOI ready: {population["valid_cell_count"]} valid10m cells, {population["valid_population_total"]:.2f} modelled people; full-countrychecksum stillpending'
    update(job, detail, status='complete', outputs=[str(DERIVED / 'population_10m.tif'), str(DERIVED / 'population_meta.json')])
    print(detail, flush=True)
    return 0


def run():
    grid = grid_contract()
    update(JOB, 'Constructing directed road graph from cached real OSM node IDs', status='running')
    osm = json.loads((RAW / 'osm_aoi.json').read_text())
    graph = build_graph(osm['elements'], grid['origin'], (0, 0, grid['width']*10, grid['height']*10))
    graph['meta']['snapshot_time'] = osm.get('osm3s', {}).get('timestamp_osm_base')
    for directory in (DERIVED, WEB_DATA):
        save_json(directory / 'road_graph.json', graph)
    facilities, facilities_status = fetch_facilities()
    stations = build_stations(facilities, grid, graph)
    context = {'schema_version': 1, 'generated_at': stamp(), 'coordinate_system': 'local_metres_east_south',
        'origin_utm': grid['origin'], 'crs': CRS, 'stations': stations, 'counts': dict(Counter(s['kind'] for s in stations)),
        'facilities_status': facilities_status, 'facilities_fetch_errors': facilities.get('fetch_errors', []),
        'source': 'OpenStreetMap contributors', 'license': 'ODbL',
        'source_url': 'https://www.openstreetmap.org/copyright', 'snapshot_time': facilities.get('osm3s', {}).get('timestamp_osm_base'),
        'facilities_note': 'Mapped facilities only; operation, staffing, fleet, capacity and flood-time availability are unknown. Schools are potential relief sites, not designated camps.',
        'road_graph': {'url': '/data/road_graph.json', **graph['meta']}, 'population': {'available': False, 'status': 'pending'}}
    for directory in (DERIVED, WEB_DATA):
        save_json(directory / 'response_context.json', context)
    context['population'] = prepare_population(grid)
    for directory in (DERIVED, WEB_DATA):
        save_json(directory / 'response_context.json', context)
    detail = f'{len(stations)} real facilities; {len(graph["nodes"])} nodes, {len(graph["edges"])} directed edges; population {context["population"]["status"]}'
    update(JOB, detail, status='complete', outputs=[str(DERIVED / 'response_context.json'), str(DERIVED / 'road_graph.json')])
    print(detail, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'population', 'clip-partial'])
    args = parser.parse_args()
    raise SystemExit(download_population() if args.command == 'population' else clip_partial_population() if args.command == 'clip-partial' else run())
