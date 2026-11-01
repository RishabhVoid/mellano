"""Acquire only explicitly licensed public ODM images and inspect USGS EPT coverage."""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
import requests
from PIL import Image
from shapely.geometry import Point,shape,box
import sys
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
from sim.jobs import update
from sim.drone_proof import ROOT,write_json

NAME='aukerman'
BASE=ROOT/'data'/'raw'/'drone_proof'/'odm_aukerman'
REPO='OpenDroneMap/odm_data_aukerman'
session=requests.Session();session.headers['User-Agent']='Otec public scientific data preparation'
def get(url):
    response=session.get(url,timeout=(20,180));response.raise_for_status();return response
def gps_exif(path):
    with Image.open(path) as image:
        exif=image.getexif();gps=exif.get_ifd(34853);detail=exif.get_ifd(34665)
        def dms(value):return float(value[0])+float(value[1])/60+float(value[2])/3600
        latitude=dms(gps[2])*(-1 if gps[1]=='S' else 1);longitude=dms(gps[4])*(-1 if gps[3]=='W' else 1)
        altitude=float(gps[6])*(-1 if gps.get(5)==b'\x01' or gps.get(5)==1 else 1)
        result={'latitude':latitude,'longitude':longitude,'altitude_m':altitude,'altitude_reference':'EXIF GPSAltitudeRef sea level; exact vertical datum unspecified','captured_local':str(detail.get(36867) or exif.get(306) or ''),'camera_make':str(exif.get(271,'')),'camera_model':str(exif.get(272,'')),'width':image.width,'height':image.height,'focal_length_mm':float(detail[37386]) if detail.get(37386) else None}
        if gps.get(29) and gps.get(7):
            stamp=str(gps[29]).replace(':','-')+'T'+':'.join(f'{int(v):02d}' for v in gps[7])+'Z'
            try:datetime.fromisoformat(stamp.replace('Z','+00:00'));result['acquired_at']=stamp
            except ValueError:pass
        return result
def download(record,commit):
    name=record['name'];assert re.fullmatch(r'[A-Za-z0-9_-]+\.(?:JPG|jpg|jpeg|JPEG)',name)
    target=BASE/'images'/name
    if not target.is_file():
        response=get(f'https://raw.githubusercontent.com/{REPO}/{commit}/images/{name}')
        temporary=target.with_suffix('.part');temporary.write_bytes(response.content);temporary.replace(target)
    content=target.read_bytes();blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
    if blob!=record['sha']:raise ValueError('Git image blob does not match pinned source')
    return {'name':name,'path':str(target.relative_to(ROOT)),'sha256':hashlib.sha256(content).hexdigest(),'source_url':f'https://github.com/{REPO}/blob/{commit}/images/{name}',**gps_exif(target)}
def main():
    BASE.mkdir(parents=True,exist_ok=True);(BASE/'images').mkdir(exist_ok=True)
    update('job_c','Checking public ODM aukerman CC0 source and exact EXIF/GPS',status='running')
    commit='4e8031630f4193494c79b1c1d3524108826d1ba9'  # Exact licensed source verified for this proof
    license_url=f'https://raw.githubusercontent.com/{REPO}/{commit}/license.txt'
    license_text=get(license_url).text
    if 'CC0 1.0 Universal' not in license_text:raise ValueError('Explicit CC0 license was not found')
    (BASE/'LICENSE.txt').write_text(license_text)
    records=get(f'https://api.github.com/repos/{REPO}/contents/images?ref={commit}').json()
    records=[r for r in records if r.get('type')=='file' and re.fullmatch(r'[A-Za-z0-9_-]+\.(?:JPG|jpg|jpeg|JPEG)',r.get('name',''))]
    if len(records)<8:raise ValueError('Too few public photos in exact images directory')
    first=download(records[0],commit);write_json(BASE/'first_image.json',first)
    print('First real image GPS',first['latitude'],first['longitude'],'camera',first['camera_model'],'date',first.get('acquired_at',first['captured_local']),flush=True)
    boundary_url='https://raw.githubusercontent.com/hobu/usgs-lidar/master/boundaries/resources.geojson'
    coverage=get(boundary_url).json();(BASE/'usgs_coverage.geojson').write_text(json.dumps(coverage))
    point=Point(first['longitude'],first['latitude']);matches=[feature for feature in coverage['features'] if shape(feature['geometry']).covers(point)]
    write_json(BASE/'coverage_candidates.json',{'source_url':boundary_url,'first_image':first,'features':matches})
    if not matches:raise ValueError('Candidate image does not fall inside any documented public USGS EPT coverage')
    print('Matching USGS coverage',json.dumps([feature['properties'] for feature in matches]),flush=True)
    update('job_c',f'Confirmed aukerman CC0 and EXIF GPS inside USGS EPT coverage; downloading {len(records)} original images',status='running')
    results=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(download,record,commit) for record in records]
        for future in as_completed(futures):
            results.append(future.result());print('Downloaded original images',len(results),'of',len(records),flush=True)
            if len(results)%10==0:update('job_c',f'Downloaded and hash-verified {len(results)}/{len(records)} real aukerman images',status='running')
    results.sort(key=lambda item:item['name'])
    bounds=[min(r['longitude'] for r in results),min(r['latitude'] for r in results),max(r['longitude'] for r in results),max(r['latitude'] for r in results)]
    full_matches=[feature for feature in coverage['features'] if shape(feature['geometry']).covers(box(*bounds))]
    manifest={'dataset':NAME,'repository':f'https://github.com/{REPO}','commit':commit,'license':'CC0-1.0','license_url':license_url,'retrieved_at':datetime.now(timezone.utc).isoformat(),'images':results,'camera_gps_bounds_wgs84':bounds,'coverage_source_url':boundary_url,'covering_ept_projects':full_matches,'candidate_decision':'Aukerman checked first: explicit CC0; GPS verified from original JPEG EXIF; full camera envelope coverage checked. Toledo and Waterbury have no explicit license on their repository overview; Seneca is CC0 but NIR farm imagery has weaker building-validation content.'}
    write_json(BASE/'source.json',manifest)
    print('Source acquisition complete:',len(results),'images; full-envelope EPT projects',json.dumps([feature['properties'] for feature in full_matches]),flush=True)
    update('job_c',f'Acquired {len(results)} hash-verified aukerman images; EPT project selection and fresh reconstruction next',status='running')
if __name__=='__main__':main()
