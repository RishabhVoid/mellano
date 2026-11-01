"""Area selection and durable server-build metadata. No implicit worker or network calls."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import time
import uuid
from pyproj import Geod, Transformer
from sim.config import ROOT, WEB_DATA, AOI

MODEL_VERSION='otec-diorama-v1'
DATA_POLICY_VERSION='real-public-data-v1'
REGIONAL_SOURCE_BBOX=[77.,28.,78.,29.]
MAX_AREA_KM2=625.
MAX_SIDE_KM=30.
MIN_SIDE_KM=.1
AREA_RE=r'area_[a-f0-9]{20}'
JOB_RE=r'build_[a-f0-9]{24}'
MODEL_RE=r'model_[a-f0-9]{24}'
CACHE=ROOT/'data'/'cache'/'areas'
PUBLISHED=WEB_DATA/'areas'/'models'
GEOD=Geod(ellps='WGS84')


def stamp():return datetime.now(timezone.utc).isoformat()
def packed(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def hash_value(value):return hashlib.sha256(packed(value).encode()).hexdigest()
def identifier(value,pattern):
    if not isinstance(value,str) or not re.fullmatch(pattern,value):raise ValueError('Invalid identifier')
    return value

def safe_path(value):
    path=Path(value).absolute()
    if any('.env' in part.lower() or part.lower().endswith('.env') for part in path.parts):raise ValueError('Protected path is not accepted')
    if '..' in path.parts or path.is_symlink():raise ValueError('Parent traversal and symlinks are not accepted')
    return path

def read_json(path):return json.loads(safe_path(path).read_text())
def write_json(path,value):
    path=safe_path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp');temporary.write_text(packed(value)+'\n');temporary.replace(path)

@lru_cache(maxsize=32)
def _digest(path,size,mtime):
    result=hashlib.sha256()
    with safe_path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()

def digest(path):
    path=safe_path(path);stat=path.stat()
    return _digest(str(path),stat.st_size,stat.st_mtime_ns)

def selection(request):
    if not isinstance(request,dict) or set(request)-{'bbox','center','size_km'}:raise ValueError('Use bbox or center and size_km only')
    if request.get('bbox') is not None:
        if request.get('center') is not None or request.get('size_km') is not None:raise ValueError('Choose bbox or center/size, not both')
        raw=request['bbox']
        if not isinstance(raw,(tuple,list)) or len(raw)!=4:raise ValueError('bbox must be [west,south,east,north]')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in raw):raise ValueError('bbox coordinates must be numbers')
        bbox=[float(v) for v in raw]
    else:
        center=request.get('center');size=request.get('size_km')
        if not isinstance(center,(tuple,list)) or len(center)!=2 or isinstance(size,bool):raise ValueError('center and size_km are required')
        lon,lat=map(float,center);size=float(size)
        if not all(math.isfinite(v) for v in (lon,lat,size)) or not -180<=lon<=180 or not -80<lat<84 or not MIN_SIDE_KM<=size<=MAX_SIDE_KM:raise ValueError('Invalid center or size')
        west=GEOD.fwd(lon,lat,270,size*500)[0];east=GEOD.fwd(lon,lat,90,size*500)[0];south=GEOD.fwd(lon,lat,180,size*500)[1];north=GEOD.fwd(lon,lat,0,size*500)[1]
        bbox=[west,south,east,north]
    if not all(math.isfinite(v) for v in bbox):raise ValueError('Area coordinates must be finite')
    west,south,east,north=bbox
    if not (-180<=west<east<=180 and -80<south<north<84):raise ValueError('Use an ordered bbox within UTM latitudes; antimeridian crossing is unsupported')
    mid=(south+north)/2;width=GEOD.inv(west,mid,east,mid)[2]/1000;height=GEOD.inv((west+east)/2,south,(west+east)/2,north)[2]/1000
    area=abs(GEOD.polygon_area_perimeter([west,east,east,west],[south,south,north,north])[0])/1e6
    if min(width,height)<MIN_SIDE_KM or max(width,height)>MAX_SIDE_KM or area>MAX_AREA_KM2:raise ValueError(f'Choose sides {MIN_SIDE_KM:g}–{MAX_SIDE_KM:g}km and area at most {MAX_AREA_KM2:g}km²')
    bbox=[round(v,6) for v in bbox];area_id='area_'+hash_value(bbox)[:20];zone=min(60,max(1,int(((west+east)/2+180)//6)+1));crs=f'EPSG:{32600+zone if (south+north)/2>=0 else 32700+zone}'
    return {'area_id':area_id,'bbox':bbox,'area_km2':area,'width_km':width,'height_km':height,'suggested_crs':crs}

def contains(outer,inner):return outer[0]-1e-7<=inner[0] and outer[1]-1e-7<=inner[1] and outer[2]+1e-7>=inner[2] and outer[3]+1e-7>=inner[3]

def clip_descriptor(region,model):
    terrain=model['terrain'];west,south,east,north=region['bbox'];project=Transformer.from_crs(4326,terrain['crs'],always_xy=True)
    # Densify all four sides so projected bounds do not depend on corner-only approximation.
    points=[]
    for i in range(17):
        f=i/16;points.extend([(west+(east-west)*f,south),(west+(east-west)*f,north),(west,south+(north-south)*f),(east,south+(north-south)*f)])
    xy=[project.transform(*point) for point in points];origin=terrain['origin'];cell=terrain['cell_size_m'];extent=[terrain['width']*cell,terrain['height']*cell]
    bounds=[max(0,min(p[0]-origin[0] for p in xy)),max(0,min(origin[1]-p[1] for p in xy)),min(extent[0],max(p[0]-origin[0] for p in xy)),min(extent[1],max(origin[1]-p[1] for p in xy))]
    subset=region['bbox']!=model['bbox']
    metrics_available=bool(model['assets'].get('product_catalog_url'))
    return {'schema_version':1,'area_id':region['area_id'],'model_id':model['model_id'],'status':'ready','requested_bbox':region['bbox'],'cached_bbox':model['bbox'],'clip_bounds_local_m':bounds,'clip_indices':[math.floor(bounds[0]/cell),math.floor(bounds[1]/cell),math.ceil(bounds[2]/cell),math.ceil(bounds[3]/cell)],'clip_definition':'Projected bounding rectangle of requested WGS84 area, in the cached model east/south metre coordinates.','subset':subset,'metrics_scope':'full_cached_aoi' if metrics_available else 'unavailable_for_model','metrics_bbox':model['bbox'],'metrics_note':'Viewing a subset does not recompute population, disaster or response metrics. Existing metrics refer to the full cached model.' if metrics_available else 'This model has terrain/buildings/context only; disaster and response metrics have not been computed.','coordinate_system':'local_metres_east_south','terrain':terrain,'assets':model['assets'],'capabilities':model.get('capabilities',{'terrain':True,'buildings':True,'disaster_runs':metrics_available}),'data_version':model['data_version'],'model_version':MODEL_VERSION,'input_data_timestamps':model.get('input_data_timestamps',{}),'provenance':model.get('provenance',{}),'evidence_note':'Area/model availability is separate from authoritative incident evidence; hypothetical scenarios are not incident reports.'}

def cached_delhi():
    paths={name:safe_path(WEB_DATA/name) for name in ('terrain.json','terrain.bin','buildings.json')}
    if not all(path.is_file() for path in paths.values()):return None
    terrain=read_json(paths['terrain.json']);size=terrain.get('width',0)*terrain.get('height',0)*4
    if size<=0 or paths['terrain.bin'].stat().st_size!=size:return None
    grid={key:terrain[key] for key in ('width','height','cell_size_m','origin','crs','min_height_m','max_height_m')}
    hashes={name:digest(path) for name,path in paths.items()};data_version=hash_value(hashes);model_id='model_'+hash_value({'data':data_version,'model':MODEL_VERSION})[:24]
    source=terrain.get('source',{});bbox=list(source.get('aoi',AOI))
    assets={'terrain_json_url':'/data/terrain.json','terrain_url':'/data/terrain.bin','buildings_url':'/data/buildings.json'}
    for filename,key in [('context.json','context_url'),('landcover.bin','landcover_url'),('embankments.json','embankments_url'),('response_context.json','response_context_url'),('product/catalog.json','product_catalog_url')]:
        if (WEB_DATA/filename).is_file():assets[key]='/data/'+filename
    return {'model_id':model_id,'bbox':bbox,'terrain':grid,'assets':assets,'data_version':data_version,'model_version':MODEL_VERSION,'content_sha256':hashes,'input_data_timestamps':{'terrain_downloaded_at':source.get('terrain',{}).get('downloaded_at'),'osm_snapshot_at':source.get('osm',{}).get('timestamp'),'export_created_at':source.get('created_at')},'provenance':{'terrain':source.get('terrain',{}),'osm':source.get('osm',{})}}

SCHEMA='''CREATE TABLE IF NOT EXISTS area_jobs (job_id TEXT PRIMARY KEY,area_id TEXT NOT NULL,cache_key TEXT UNIQUE NOT NULL,request_json TEXT NOT NULL,status TEXT NOT NULL,model_id TEXT,descriptor_json TEXT,worker_id TEXT,lease_until DOUBLE PRECISION,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,created_at DOUBLE PRECISION NOT NULL,updated_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS area_models (model_id TEXT PRIMARY KEY,descriptor_json TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS area_job_region ON area_jobs(area_id,created_at);'''

class Store:
    """SQLite default; optional PostgreSQL/Yugabyte through process-provided DSN only."""
    def __init__(self,path=None,dsn=None):
        self.path=safe_path(path or CACHE/'metadata.sqlite3');self.dsn=dsn;self.backend='postgresql' if dsn else 'sqlite';self.warning=None
        self.driver=None
        if dsn:
            if not dsn.startswith(('postgresql://','postgres://')):raise ValueError('Database DSN must be a PostgreSQL URI')
            try:import psycopg;self.driver=psycopg
            except ImportError:raise RuntimeError('PostgreSQL driver unavailable; configured metadata database cannot be used.') from None
        with self.connection() as con:
            for statement in SCHEMA.split(';'):
                if statement.strip():self.execute(con,statement)
    @contextmanager
    def connection(self):
        if self.backend=='sqlite':
            self.path.parent.mkdir(parents=True,exist_ok=True);con=sqlite3.connect(self.path,timeout=15);con.row_factory=sqlite3.Row;con.execute('PRAGMA journal_mode=WAL');con.execute('BEGIN IMMEDIATE')
        else:
            from psycopg.rows import dict_row
            try:con=self.driver.connect(self.dsn,connect_timeout=5,row_factory=dict_row)
            except Exception:raise RuntimeError('Configured metadata database is unavailable; credentials were not logged') from None
        try:yield con;con.commit()
        except BaseException:con.rollback();raise
        finally:con.close()
    def execute(self,con,sql,args=()):return con.execute(sql.replace('?','%s') if self.backend=='postgresql' else sql,args)
    def get(self,job_id):
        identifier(job_id,JOB_RE)
        with self.connection() as con:
            row=self.execute(con,'SELECT * FROM area_jobs WHERE job_id=?',(job_id,)).fetchone()
            return dict(row) if row else None
    def latest(self,area_id):
        identifier(area_id,AREA_RE)
        with self.connection() as con:
            row=self.execute(con,'SELECT * FROM area_jobs WHERE area_id=? ORDER BY created_at DESC LIMIT 1',(area_id,)).fetchone()
            return dict(row) if row else None
    def model(self,model_id):
        identifier(model_id,MODEL_RE)
        with self.connection() as con:
            row=self.execute(con,'SELECT descriptor_json FROM area_models WHERE model_id=?',(model_id,)).fetchone()
            return json.loads(row['descriptor_json']) if row else None
    def cached_models(self):
        with self.connection() as con:
            rows=self.execute(con,'SELECT descriptor_json FROM area_models ORDER BY created_at DESC LIMIT 128').fetchall()
            return [json.loads(row['descriptor_json']) for row in rows]
    def admit(self,region,model=None):
        key=hash_value({'region':region['bbox'],'model_version':MODEL_VERSION,'data_policy_version':DATA_POLICY_VERSION,'cached_data_version':model['data_version'] if model else None});job_id='build_'+key[:24];now=time.time();descriptor=clip_descriptor(region,model) if model else None
        with self.connection() as con:
            request={**region,'model_version':MODEL_VERSION,'data_policy_version':DATA_POLICY_VERSION}
            self.execute(con,'INSERT INTO area_jobs(job_id,area_id,cache_key,request_json,status,model_id,descriptor_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO NOTHING',(job_id,region['area_id'],key,packed(request),'ready' if model else 'queued',model['model_id'] if model else None,packed(descriptor) if descriptor else None,now,now))
            if model:self.execute(con,'INSERT INTO area_models(model_id,descriptor_json,created_at) VALUES(?,?,?) ON CONFLICT(model_id) DO NOTHING',(model['model_id'],packed(model),now))
        return self.get(job_id)
    def claim(self,worker_id,lease_s=120):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',worker_id):raise ValueError('Invalid worker id')
        now=time.time()
        with self.connection() as con:
            # An expired worker becomes interrupted; never silently run a duplicate builder.
            self.execute(con,"UPDATE area_jobs SET status='interrupted',error='Worker lease expired; operator must verify the previous builder stopped before requeueing',updated_at=? WHERE status='running' AND lease_until<?",(now,now))
            rows=self.execute(con,"SELECT job_id FROM area_jobs WHERE status='queued' ORDER BY created_at LIMIT 16").fetchall()
            for row in rows:
                result=self.execute(con,"UPDATE area_jobs SET status='running',worker_id=?,lease_until=?,attempts=attempts+1,updated_at=?,error=NULL WHERE job_id=? AND status='queued'",(worker_id,now+lease_s,now,row['job_id']))
                if result.rowcount==1:claimed=row['job_id'];break
            else:return None
        return self.get(claimed)
    def heartbeat(self,job_id,worker_id):
        with self.connection() as con:
            result=self.execute(con,"UPDATE area_jobs SET lease_until=?,updated_at=? WHERE job_id=? AND worker_id=? AND status='running'",(time.time()+120,time.time(),job_id,worker_id))
            return result.rowcount==1
    def finish(self,job_id,worker_id,status,error=None,model=None):
        if status not in ('ready','failed','interrupted','server_required'):raise ValueError('Invalid terminal worker state')
        current=self.get(job_id)
        if not current:raise ValueError('Unknown job')
        descriptor=clip_descriptor(json.loads(current['request_json']),model) if model else None
        with self.connection() as con:
            changed=self.execute(con,'UPDATE area_jobs SET status=?,error=?,model_id=?,descriptor_json=?,lease_until=NULL,updated_at=? WHERE job_id=? AND worker_id=? AND status=?',(status,error,model['model_id'] if model else None,packed(descriptor) if descriptor else None,time.time(),job_id,worker_id,'running'))
            if changed.rowcount!=1:raise ValueError('Job lease is no longer owned by this worker')
            if model:self.execute(con,'INSERT INTO area_models(model_id,descriptor_json,created_at) VALUES(?,?,?) ON CONFLICT(model_id) DO NOTHING',(model['model_id'],packed(model),time.time()))
    def requeue(self,job_id):
        identifier(job_id,JOB_RE)
        with self.connection() as con:
            changed=self.execute(con,"UPDATE area_jobs SET status='queued',worker_id=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE job_id=? AND status IN ('failed','interrupted','server_required')",(time.time(),job_id))
            return changed.rowcount==1

@lru_cache(maxsize=1)
def get_store():return Store(dsn=os.environ.get('OTEC_DATABASE_URL'))

def public_status(row):
    if row is None:raise KeyError('Unknown area job')
    ready=row['status']=='ready';result={'area_id':row['area_id'],'job_id':row['job_id'],'model_id':row['model_id'],'status':row['status'],'execution':'cached' if ready else 'explicit_server_worker' if row['status']=='running' else 'remote_server_required','reason':row.get('error') or (None if ready else 'No automatic local build. A configured, explicitly started server worker must provide real data and validated model assets.'),'status_url':f"/areas/jobs/{row['job_id']}",'descriptor_url':f"/areas/{row['area_id']}/descriptor",'created_at':datetime.fromtimestamp(row['created_at'],timezone.utc).isoformat(),'updated_at':datetime.fromtimestamp(row['updated_at'],timezone.utc).isoformat(),'attempts':row['attempts']}
    if row.get('descriptor_json'):result['descriptor']=json.loads(row['descriptor_json'])
    from sim.regions import pack_for_bbox
    region=json.loads(row['request_json']);result['regional_source_supported']=pack_for_bbox(region['bbox']) is not None
    if not ready and not result['regional_source_supported']:result['reason']+=' This area is outside the registered regional source packs; another verified source pack is required.'
    return result

def catalog(store=None):
    store=store or get_store();model=cached_delhi()
    return {'schema_version':1,'limits':{'max_area_km2':MAX_AREA_KM2,'max_side_km':MAX_SIDE_KM,'min_side_km':MIN_SIDE_KM,'latitude_range':[-80,84],'antimeridian_supported':False},'model_version':MODEL_VERSION,'data_policy_version':DATA_POLICY_VERSION,'cached':[{'label':'Delhi · Yamuna AOI',**model}] if model else [],'worker':{'automatic_local_builds':False,'connected':False,'execution':'explicit_server_worker','global_builder_available':False,'regional_builder':'sim.area_builder','regional_source_bbox':REGIONAL_SOURCE_BBOX,'regional_source_tile':'N28E077_FABDEM_V1-2.tif','note':'No server is connected by this application. An explicitly started server worker can use the verified regional source pack and real OSM. Other areas require another source pack or configured trusted builder.'},'metadata':{'backend':store.backend,'warning':store.warning}}

def select_area(request,store=None):
    store=store or get_store();region=selection(request);model=cached_delhi()
    if model and not contains(model['bbox'],region['bbox']):model=None
    # Previously published server artifacts can satisfy contained views too.
    if model is None:
        prior=store.latest(region['area_id'])
        if prior and prior['status']=='ready':
            request=json.loads(prior['request_json'])
            if request.get('model_version')==MODEL_VERSION and request.get('data_policy_version')==DATA_POLICY_VERSION:return public_status(prior)
        for candidate in store.cached_models():
            if candidate.get('model_version')==MODEL_VERSION and candidate.get('data_policy_version')==DATA_POLICY_VERSION and contains(candidate['bbox'],region['bbox']):model=candidate;break
    return public_status(store.admit(region,model))


def validate_export(directory,region):
    """Validate concrete exporter assets; provenance is required, never fabricated."""
    directory=safe_path(directory);metadata=read_json(directory/'area-model.json')
    if metadata.get('synthetic') is not False:raise ValueError('Export must explicitly identify non-synthetic data')
    provenance=metadata.get('provenance')
    if not isinstance(provenance,list) or not provenance or any(not isinstance(p,dict) or not all(p.get(k) for k in ('source','source_url','license')) for p in provenance):raise ValueError('Real source, URL and license provenance is required')
    bounds=selection({'bbox':metadata.get('bbox')})['bbox']
    if not contains(bounds,region['bbox']):raise ValueError('Export does not cover requested area')
    terrain=read_json(directory/'terrain.json');width=terrain.get('width');height=terrain.get('height');cell=terrain.get('cell_size_m');origin=terrain.get('origin')
    if isinstance(width,bool) or isinstance(height,bool) or not isinstance(width,int) or not isinstance(height,int) or not 2<=width<=10000 or not 2<=height<=10000 or width*height>10000000:raise ValueError('Invalid terrain dimensions')
    if not isinstance(cell,(int,float)) or not math.isfinite(cell) or not 1<=cell<=100 or not isinstance(origin,list) or len(origin)!=2 or not all(math.isfinite(float(v)) for v in origin):raise ValueError('Invalid projected terrain grid')
    project=Transformer.from_crs(terrain.get('crs'),4326,always_xy=True)
    if project.source_crs is None or not project.source_crs.is_projected or any(abs(axis.unit_conversion_factor-1)>1e-9 for axis in project.source_crs.axis_info[:2]):raise ValueError('Terrain requires a projected metre CRS')
    to_grid=Transformer.from_crs(4326,terrain['crs'],always_xy=True)
    west,south,east,north=region['bbox']
    for lon,lat in ((west,south),(west,north),(east,south),(east,north)):
        x,y=to_grid.transform(lon,lat)
        if not origin[0]-.01<=x<=origin[0]+width*cell+.01 or not origin[1]-height*cell-.01<=y<=origin[1]+.01:raise ValueError('Terrain grid does not actually cover the requested coordinates')
    binary=safe_path(directory/'terrain.bin')
    if binary.stat().st_size!=width*height*4:raise ValueError('Terrain binary length differs from grid')
    with binary.open('rb') as stream:
        for chunk in iter(lambda:stream.read(65536),b''):
            if any(not math.isfinite(v[0]) for v in struct.iter_unpack('<f',chunk)):raise ValueError('Terrain contains invalid heights')
    buildings=read_json(directory/'buildings.json')
    if not isinstance(buildings,dict) or not isinstance(buildings.get('buildings'),list):raise ValueError('Real building export array is required')
    for building in buildings['buildings']:
        if not isinstance(building,dict) or not building.get('id') or not building.get('footprint_source'):raise ValueError('Building source identity is required')
        rings=building.get('footprint')
        if not isinstance(rings,list) or not rings or any(not isinstance(r,list) or len(r)<4 for r in rings):raise ValueError('Invalid building polygon')
        for ring in rings:
            if ring[0]!=ring[-1] or any(not isinstance(p,list) or len(p)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) for p in ring):raise ValueError('Building polygon must be finite and closed')
        value=building.get('height')
        if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value) or value<0):raise ValueError('Unknown heights must be null, not fabricated')
    files=['terrain.json','terrain.bin','buildings.json','area-model.json']
    for name in ('context.json','landcover.bin','embankments.json','response_context.json','road_graph.json'):
        if safe_path(directory/name).is_file():files.append(name)
    if 'landcover.bin' in files and (directory/'landcover.bin').stat().st_size!=width*height:raise ValueError('Mapped landcover length differs from terrain')
    for name in ('context.json','embankments.json'):
        if name in files:read_json(directory/name)
    hashes={name:digest(directory/name) for name in files};data_version=hash_value(hashes);model_id='model_'+hash_value({'data':data_version,'model':MODEL_VERSION})[:24]
    result={'model_id':model_id,'bbox':bounds,'terrain':{key:terrain[key] for key in ('width','height','cell_size_m','origin','crs')},'data_version':data_version,'data_policy_version':DATA_POLICY_VERSION,'model_version':MODEL_VERSION,'content_sha256':hashes,'input_data_timestamps':metadata.get('input_data_timestamps',{}),'provenance':provenance,'capabilities':metadata.get('capabilities',{'terrain':True,'buildings':True,'disaster_runs':False,'operational_response':False}),'limitations':metadata.get('limitations',[])}
    return result,files

def publish_export(directory,region,published=PUBLISHED):
    model,files=validate_export(directory,region);published=safe_path(published);published.mkdir(parents=True,exist_ok=True);destination=published/model['model_id']
    if not destination.exists():
        temporary=published/(model['model_id']+'.'+uuid.uuid4().hex+'.tmp');temporary.mkdir()
        try:
            for name in files:shutil.copyfile(safe_path(Path(directory)/name),temporary/name)
            if any(digest(temporary/name)!=model['content_sha256'][name] for name in files):raise ValueError('Source changed while publishing')
            try:temporary.rename(destination)
            except FileExistsError:shutil.rmtree(temporary)
        except BaseException:
            if temporary.exists():shutil.rmtree(temporary)
            raise
    prefix='/data/areas/models/'+model['model_id']+'/'
    model['assets']={'terrain_json_url':prefix+'terrain.json','terrain_url':prefix+'terrain.bin','buildings_url':prefix+'buildings.json'}
    for filename,key in (('context.json','context_url'),('landcover.bin','landcover_url'),('embankments.json','embankments_url'),('response_context.json','response_context_url'),('road_graph.json','road_graph_url')):
        if filename in files:model['assets'][key]=prefix+filename
    write_json(destination/'manifest.json',model);return model

def worker_once(store,worker_id,command=None,workspace_root=None,timeout_s=3600,published=PUBLISHED):
    if not 1<=timeout_s<=86400:raise ValueError('Invalid worker timeout')
    row=store.claim(worker_id)
    if row is None:return {'status':'idle'}
    job_id=row['job_id'];region=json.loads(row['request_json'])
    if not command:
        store.finish(job_id,worker_id,'server_required','No real-data builder configured. This installation does not provide a universal area builder.')
        return public_status(store.get(job_id))
    workspace=safe_path(workspace_root or CACHE/'work')/job_id/uuid.uuid4().hex;workspace.mkdir(parents=True);output=workspace/'export';output.mkdir();config=workspace/'area-request.json'
    write_json(config,{'schema_version':1,**region,'model_version':MODEL_VERSION,'data_policy_version':DATA_POLICY_VERSION,'output_directory':str(output),'resource_limits':{'threads':2,'nice':15},'requirements':['Real terrain and building sources with provenance','Unknown building heights remain null','No environment-file access or placeholder assets'],'export_contract':{'files':['area-model.json','terrain.json','terrain.bin','buildings.json'],'area-model.json':{'bbox':'WGS84 coverage','synthetic':False,'provenance':[{'source':'dataset name','source_url':'authoritative source URL','license':'actual source license'}]}}})
    process=None
    try:
        child_env={name:os.environ[name] for name in ('PATH','LANG','LC_ALL','TMPDIR','SYSTEMROOT') if name in os.environ}
        child_env.update({name:'2' for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')})
        argv=[*command,'--area-config',str(config),'--output',str(output)]
        if os.name=='posix':argv=['/usr/bin/nice','-n','15',*argv]
        with (workspace/'builder.log').open('wb') as log:
            process=subprocess.Popen(argv,cwd=workspace,env=child_env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            started=time.monotonic();last_heartbeat=started
            while process.poll() is None:
                if time.monotonic()-started>timeout_s:raise TimeoutError('Trusted builder exceeded configured timeout')
                if time.monotonic()-last_heartbeat>20:
                    if not store.heartbeat(job_id,worker_id):raise RuntimeError('Worker lease revoked')
                    last_heartbeat=time.monotonic()
                time.sleep(.2)
        if process.returncode:raise RuntimeError('Trusted builder failed; inspect its operator-only workspace log')
        model=publish_export(output,region,published=published);store.finish(job_id,worker_id,'ready',model=model)
    except BaseException as error:
        if process is not None and process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        interrupted=isinstance(error,(KeyboardInterrupt,SystemExit));message='Worker interrupted; partial export was not published' if interrupted else str(error) if isinstance(error,(ValueError,TimeoutError,RuntimeError)) else 'Build failed; inspect operator-only workspace log'
        store.finish(job_id,worker_id,'interrupted' if interrupted else 'failed',error=message)
        if interrupted:raise
    return public_status(store.get(job_id))

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('catalog')
    p=sub.add_parser('select');p.add_argument('--bbox',nargs=4,type=float,required=True)
    p=sub.add_parser('status');p.add_argument('job_id')
    p=sub.add_parser('requeue');p.add_argument('job_id')
    p=sub.add_parser('worker');mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--once',action='store_true');mode.add_argument('--watch',action='store_true');p.add_argument('--poll-seconds',type=int,default=10);p.add_argument('--worker-id',default='server-worker');p.add_argument('--workspace-root');p.add_argument('--timeout-s',type=int,default=3600);p.add_argument('--source-tile');p.add_argument('--command',nargs=argparse.REMAINDER)
    p=sub.add_parser('publish');p.add_argument('job_id');p.add_argument('--export',required=True)
    args=parser.parse_args();store=get_store()
    if args.action=='catalog':result=catalog(store)
    elif args.action=='select':result=select_area({'bbox':args.bbox},store)
    elif args.action=='status':result=public_status(store.get(args.job_id))
    elif args.action=='requeue':result={'requeued':store.requeue(args.job_id)}
    elif args.action=='worker':
        if not 5<=args.poll_seconds<=60:raise ValueError('Worker polling must be5–60seconds')
        command=args.command
        if command and args.source_tile:raise ValueError('Choose regional source tile or custom builder command')
        if args.source_tile:command=[sys.executable,str(Path(__file__).with_name('area_builder.py')),'--source-tile',str(safe_path(args.source_tile))]
        def stop_worker(signum,frame):raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,stop_worker)
        try:
            while True:
                result=worker_once(store,args.worker_id,command,args.workspace_root,args.timeout_s)
                if args.once:break
                if result['status']=='idle':time.sleep(args.poll_seconds)
        except KeyboardInterrupt:result={'status':'interrupted','reason':'Explicit server worker stopped; no automatic restart'}
    else:
        row=store.get(args.job_id)
        if not row:raise ValueError('Unknown job')
        if row['status'] not in ('queued','server_required','failed','interrupted'):raise ValueError('Only an idle job may be published manually')
        store.requeue(args.job_id)
        with store.connection() as con:
            changed=store.execute(con,"UPDATE area_jobs SET status='running',worker_id='operator-publish',lease_until=?,updated_at=? WHERE job_id=? AND status='queued'",(time.time()+120,time.time(),args.job_id))
            if changed.rowcount!=1:raise ValueError('Job was claimed by another worker; publication refused')
        try:model=publish_export(args.export,json.loads(row['request_json']));store.finish(args.job_id,'operator-publish','ready',model=model)
        except Exception:
            store.finish(args.job_id,'operator-publish','failed',error='Provided export failed validation; no ready model was registered');raise
        result=public_status(store.get(args.job_id))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
