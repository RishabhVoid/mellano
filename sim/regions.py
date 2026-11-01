"""Verified regional source registrations; no source downloads or secret loading."""
import json
import math
import re

from sim.config import DERIVED

DELHI = {'id': 'N28E077', 'label': 'Delhi FABDEM', 'lon_tile': 77, 'lat_tile': 28,
         'bbox': [77., 28., 78., 29.], 'tile': 'N28E077_FABDEM_V1-2.tif',
         'sha256': '6764c2c6b277a4dfda138286aca880119a755c2a2f00b836c6b91db035775050',
         'source_url': 'https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn'}


def registered_packs():
    path = DERIVED / 'region_packs.json'
    if any(p.is_symlink() for p in (path, *path.parents)): raise ValueError('Regional registry must not use symlinks')
    if not path.is_file(): return [dict(DELHI)]
    if path.stat().st_size > 64_000: raise ValueError('Regional registry exceeds size limit')
    entries = json.loads(path.read_text()).get('packs', [])
    if not isinstance(entries, list) or len(entries) > 50: raise ValueError('Invalid regional registry')
    result = [dict(DELHI)]; tiles = {(77, 28)}
    for entry in entries:
        lon, lat = entry.get('lon_tile'), entry.get('lat_tile')
        if type(lon) is not int or type(lat) is not int or not -180 <= lon < 180 or not -80 <= lat < 84:
            raise ValueError('Regional pack requires integer longitude/latitude tile coordinates')
        bbox = entry.get('bbox')
        if not isinstance(bbox, list) or bbox != [lon, lat, lon+1, lat+1] or not all(math.isfinite(v) for v in bbox):
            raise ValueError('Pack bbox must match its one-degree source tile')
        if not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')): raise ValueError('Verified source SHA256 required')
        tile = entry.get('tile', '')
        if not re.fullmatch(r'[NS][0-9]{2}[EW][0-9]{3}_FABDEM_V1-2\.tif', tile): raise ValueError('Use an explicit FABDEM tile name')
        if '.env' in str(entry.get('source_tile', '')).lower(): raise ValueError('Protected source path')
        if (lon, lat) in tiles: raise ValueError('Duplicate source tile registration')
        tiles.add((lon, lat)); result.append(entry)
    return result


def pack_for_bbox(bbox):
    for pack in registered_packs():
        west, south, east, north = pack['bbox']
        if west <= bbox[0] and south <= bbox[1] and east >= bbox[2] and north >= bbox[3]: return pack
    return None


def public_regions():
    return {'regions': [{key: pack.get(key) for key in ('id', 'label', 'bbox', 'tile', 'sha256', 'source_url')} for pack in registered_packs()],
            'note': 'Registered verified source packs; new models still require an operator worker. No global coverage claim.'}
