"""Fetch official CC0 Kenney models, retaining attribution and source hashes."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import sys
from datetime import datetime, timezone
import zipfile
import requests

ROOT=Path('/Users/rishabh/Documents/codebase/projects/hackcbs/the_one')
sys.path.insert(0,str(ROOT))
from sim.jobs import update

PACKS=[
 ('car-kit','https://opengameart.org/content/car-kit','https://opengameart.org/sites/default/files/kenney_car-kit_3.1.zip'),
 ('watercraft-kit','https://opengameart.org/content/watercraft-kit','https://opengameart.org/sites/default/files/kenney_watercraft_kit_0.zip')]
RAW=ROOT/'data/raw/kenney';PUBLIC=ROOT/'web/public/assets/vehicles'

def safe_name(name):
    p=PurePosixPath(name)
    return not p.is_absolute() and '..' not in p.parts and not any('.env' in x.lower() or x.lower().endswith('.env') for x in p.parts)

def fetch_pack(slug,page,url):
    RAW.mkdir(parents=True,exist_ok=True)
    archive=RAW/(slug+'.zip')
    if not archive.is_file():
        tmp=archive.with_suffix('.part')
        update('product_vehicle_assets','Downloading official Kenney '+slug,status='running')
        with requests.get(url,headers={'User-Agent':'Otec asset downloader; CC0 visual models'},stream=True,timeout=(15,90)) as response:
            response.raise_for_status()
            with tmp.open('wb') as target:
                size=0
                for chunk in response.iter_content(1024*1024):
                    size+=len(chunk)
                    if size>100*1024*1024:raise ValueError('Asset archive exceeds size limit')
                    target.write(chunk)
        if not zipfile.is_zipfile(tmp):raise ValueError('Asset response is not a ZIP archive')
        tmp.replace(archive)
    result={'name':slug,'source_url':page,'download_url':url,'license':'CC0 1.0','license_url':'https://creativecommons.org/publicdomain/zero/1.0/','creator':'Kenney','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'models':[]}
    directory=PUBLIC/slug;directory.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        members=[x for x in package.infolist() if not x.is_dir() and safe_name(x.filename)]
        licenses=[x for x in members if PurePosixPath(x.filename).name.lower() in ('license.txt','license.md')]
        if not licenses:raise ValueError('No archive license found')
        license_text=package.read(licenses[0]).decode('utf-8-sig')
        if 'CC0' not in license_text:raise ValueError('Archive does not confirm CC0')
        (directory/'LICENSE.txt').write_text(license_text)
        glbs=[x for x in members if x.filename.lower().endswith('.glb')]
        if not glbs:raise ValueError('No GLB models in official archive')
        for item in glbs:
            payload=package.read(item)
            if payload[:4]!=b'glTF':raise ValueError('Invalid GLB')
            json_size,chunk_type=struct.unpack('<II',payload[12:20])
            if chunk_type!=0x4E4F534A:raise ValueError('GLB missing JSON chunk')
            gltf=json.loads(payload[20:20+json_size])
            for ref in [*gltf.get('buffers',[]),*gltf.get('images',[])]:
                uri=ref.get('uri')
                if uri and not uri.startswith('data:'):
                    if not safe_name(uri) or PurePosixPath(uri).suffix.lower() not in ('.png','.jpg','.jpeg','.bin'):
                        raise ValueError('Unsafe or unsupported asset resource')
                    source=str(PurePosixPath(item.filename).parent / uri)
                    if not safe_name(source):raise ValueError('Unsafe asset archive path')
                    entry=package.getinfo(source)
                    if entry.file_size>20*1024*1024:raise ValueError('Asset resource exceeds bound')
                    target=directory/uri
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(package.read(entry))
            name=PurePosixPath(item.filename).name
            (directory/name).write_bytes(payload)
            result['models'].append({'name':name,'url':f'/assets/vehicles/{slug}/{name}','bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
    return result

if __name__=='__main__':
    if os.getpriority(os.PRIO_PROCESS,0)<15:os.setpriority(os.PRIO_PROCESS,0,15)
    sources=[fetch_pack(*pack) for pack in PACKS]
    manifest={'version':1,'downloaded_at':datetime.now(timezone.utc).isoformat(),'purpose':'CC0 visual representations of modelled response units; not observed fleets','packs':sources}
    manifest['vehicles']={'ambulance': {'url': '/assets/vehicles/car-kit/ambulance.glb', 'length_m': 5.5, 'rotation_y': 0}, 'fire_engine': {'url': '/assets/vehicles/car-kit/firetruck.glb', 'length_m': 8.0, 'rotation_y': 0}, 'police': {'url': '/assets/vehicles/car-kit/police.glb', 'length_m': 4.5, 'rotation_y': 0}, 'rescue_boat': {'url': '/assets/vehicles/watercraft-kit/boat-speed-a.glb', 'length_m': 5.0, 'rotation_y': 0}}
    manifest['display_note']='Illustrative CC0 models; dimensions and overview scale are display assumptions, not observed fleet specifications.'
    (PUBLIC/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    update('product_vehicle_assets','Downloaded official CC0 Car Kit and Watercraft Kit models; licenses and hashes recorded',status='running')
    print(json.dumps({x['name']:[m['name'] for m in x['models']] for x in sources},indent=2))
