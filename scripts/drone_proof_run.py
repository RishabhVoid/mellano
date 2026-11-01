"""New public-image proof run; real acquisitions and no previous reconstruction reuse."""
import json
import math
import os
import sys
import subprocess
import time
from pathlib import Path
# Limit numerical workers before loading scientific modules.
for name in ('OMP_NUM_THREADS','OMP_THREAD_LIMIT','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='2'
if os.getpriority(os.PRIO_PROCESS,0)<15:os.setpriority(os.PRIO_PROCESS,0,15)
import requests
from pyproj import Transformer
from shapely.geometry import shape,box,mapping
from shapely.ops import transform,unary_union
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
from sim import drone_proof as proof
from sim.jobs import update

RAW=proof.ROOT/'data'/'raw'/'drone_proof'/'odm_aukerman'
def prepare():
    source=proof.read_json(RAW/'source.json')
    if not source or not source.get('covering_ept_projects'):raise ValueError('Original images or full GPS-envelope USGS coverage not confirmed')
    if source['license']!='CC0-1.0':raise ValueError('Explicit image license not confirmed')
    project=next((item for item in source['covering_ept_projects'] if item['properties']['name']=='OH_Statewide_Phase1_1_2019'),None)
    if not project:raise ValueError('The documented Ohio EPT project does not cover every source camera')
    crs='EPSG:32617';forward=Transformer.from_crs(4326,crs,always_xy=True);inverse=Transformer.from_crs(crs,4326,always_xy=True)
    xy=[forward.transform(record['longitude'],record['latitude']) for record in source['images']]
    bounds=[math.floor(min(p[0] for p in xy)-50),math.floor(min(p[1] for p in xy)-50),math.ceil(max(p[0] for p in xy)+50),math.ceil(max(p[1] for p in xy)+50)]
    west,south,east,north=inverse.transform_bounds(*bounds,densify_pts=21)
    if not shape(project['geometry']).covers(box(west,south,east,north)):raise ValueError('Expanded reconstruction AOI extends beyond confirmed EPT coverage')
    buildings_path=RAW/'osm_buildings.geojson'
    if not buildings_path.is_file():
        query=f'[out:json][timeout:90];way["building"]({south},{west},{north},{east});out geom;'
        payload=None
        for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter'):
            try:
                response=requests.get(endpoint,params={'data':query},timeout=(20,120));response.raise_for_status();payload=response.json();break
            except requests.RequestException as error:print('Public OSM endpoint retry:',type(error).__name__,flush=True)
        if payload is None:
            import xml.etree.ElementTree as ET
            response=requests.get('https://api.openstreetmap.org/api/0.6/map',params={'bbox':','.join(map(str,[west,south,east,north]))},timeout=(20,120));response.raise_for_status()
            tree=ET.fromstring(response.content);nodes={n.attrib['id']:{'lon':float(n.attrib['lon']),'lat':float(n.attrib['lat'])} for n in tree.findall('node')}
            payload={'elements':[{'id':int(w.attrib['id']),'geometry':[nodes[n.attrib['ref']] for n in w.findall('nd')]} for w in tree.findall('way') if any(t.attrib.get('k')=='building' for t in w.findall('tag'))]}
            print('Original OSM map API building query succeeded',flush=True)
        if payload.get('remark'):raise ValueError('OSM building query incomplete')
        features=[]
        for item in payload['elements']:
            ring=[[point['lon'],point['lat']] for point in item.get('geometry',[])]
            if len(ring)>=4 and ring[0]==ring[-1]:features.append({'type':'Feature','properties':{'id':'way/'+str(item['id']),'source':'OpenStreetMap contributors','source_url':'https://www.openstreetmap.org/way/'+str(item['id'])},'geometry':{'type':'Polygon','coordinates':[ring]}})
        proof.write_json(buildings_path,{'type':'FeatureCollection','features':features,'source':'OpenStreetMap contributors, ODbL','query_bounds_wgs84':[west,south,east,north]})
    buildings=proof.read_json(buildings_path)['features']
    area=box(*bounds);held=[]
    # Split design is frozen before reconstruction. Every mapped building is held out.
    for feature in buildings:
        geometry=transform(forward.transform,shape(feature['geometry']))
        if geometry.intersects(area):held.append(geometry.buffer(12).intersection(area))
    for row,y in enumerate(range(bounds[1],bounds[3],60)):
        for column,x in enumerate(range(bounds[0],bounds[2],60)):
            if (row+column)%3==0:held.append(box(x+5,y+5,min(x+55,bounds[2]),min(y+55,bounds[3])).intersection(area))
    held=unary_union(held);fit=area.difference(held.buffer(3))
    areas=RAW/'validation_areas.geojson'
    proof.write_json(areas,{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'split':'fit','design':'Ground outside frozen held-out areas'},'geometry':mapping(fit)},{'type':'Feature','properties':{'split':'held_out','design':'All source OSM buildings plus deterministic spatial blocks; chosen before SfM'},'geometry':mapping(held)}]})
    ept_url=project['properties']['url'];metadata=RAW/'ept_metadata.json'
    if not metadata.is_file():response=requests.get(ept_url,timeout=30);response.raise_for_status();proof.write_json(metadata,response.json())
    config={'confirmed_sources':True,'image_collection':{'dataset':'aukerman','path':str((RAW/'source.json').relative_to(proof.ROOT)),'source_url':source['repository'],'license':source['license'],'license_url':source['license_url'],'capture_date':source['images'][0]['captured_local'][:10].replace(':','-'),'acquired_at':'2016-06-29 (EXIF local date; timezone unspecified)','vertical_datum':'EXIF GPSAltitudeRef sea level; exact geoid unspecified'},'lidar':{'project':'OH_Statewide_Phase1_1_2019','source_url':'https://www.fisheries.noaa.gov/inport/item/69581/full-list','ept_url':ept_url,'metadata_path':str(metadata.relative_to(proof.ROOT)),'local_path':str((RAW/'usgs_lidar_aoi.laz').relative_to(proof.ROOT)),'vertical_unit':'metre','vertical_datum':'NAVD88/GEOID12B; EPT Z converted to metres by USGS','datum_source_url':'https://www.fisheries.noaa.gov/inport/item/69581/full-list','acquisition_range':['2019-11-04','2020-05-02'],'acquired_at':'2019-11-04 to 2020-05-02 (project range; exact flight date pending)'},'output_crs':crs,'bounds':bounds,'max_image_size':1800,'validation':{'mode':'independent_surface_split','areas_geojson':str(areas.relative_to(proof.ROOT)),'geometry_crs':crs,'buildings_geojson':str(buildings_path.relative_to(proof.ROOT)),'buildings_crs':'EPSG:4326'}}
    proof.write_json(proof.MANIFEST,config)
    return config

def main():
    while not (RAW/'source.json').is_file():update('job_c_run','Waiting for original image download and full coverage verification',status='running');time.sleep(15)
    config=prepare();proof.need_sources(proof.MANIFEST)
    update('job_c_run','Fresh image selection and measured sharpness',status='running')
    run=proof.select_images(config);print('New run',run,flush=True)
    for stage,function,tools in [('sparse',proof.sparse,['colmap']),('gps_alignment',proof.align_gps,['colmap']),('dense',proof.dense,['colmap','InterfaceCOLMAP','DensifyPointCloud']),('ndsm',proof.rasterize,['pdal'])]:
        while any(proof.executable(name) is None for name in tools):update('job_c_run','Waiting for native tools before '+stage,status='running');time.sleep(15)
        proof.load_run(run);update('job_c_run','Computing fresh '+stage+'; commands and timestamps logged in '+run.name,status='running');subprocess.run([sys.executable,'-B','-m','sim.drone_proof',{'gps_alignment':'align','ndsm':'rasterize'}.get(stage,stage),'--run',str(run)],check=True,env=proof.bounded_environment())
    update('job_c_run','Computing independent held-out LiDAR diagnostics',status='running')
    subprocess.run([sys.executable,'-B','-m','sim.drone_proof','validate','--run',str(run)],check=True,env=proof.bounded_environment())
    update('job_c_run','Fresh image reconstruction and independent LiDAR validation completed',status='running')
if __name__=='__main__':main()
