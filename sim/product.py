"""Disaster → impact → response product exports, independent of the static 2023 replay."""
import argparse,hashlib,json,math,os,re,time
if __name__ == "__main__":
    for _name in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS","VECLIB_MAXIMUM_THREADS","NUMEXPR_NUM_THREADS"):
        os.environ[_name]="2"
    if os.getpriority(os.PRIO_PROCESS,0)<15:os.setpriority(os.PRIO_PROCESS,0,15)
from datetime import datetime,timezone
from functools import lru_cache
from pathlib import Path
import numpy as np
import rasterio
from scipy import ndimage
from rasterio.features import rasterize
from affine import Affine
from shapely.geometry import Polygon
from sim.config import ROOT,DERIVED,RUNS,WEB_DATA
from sim.disasters import PLUGINS,LABEL,PALETTE,STATE_CODES
from sim.response import Planner
from sim.validation import water_overlap
from sim import jobs

VERSION=4
RUN_ROOT=RUNS/'product'
PUBLIC=WEB_DATA/'product'/'runs'
INDEX=WEB_DATA/'product'/'index.json'
ID_PATTERN=r'product_[a-f0-9]{16}'
STRIDE=4
HAZARD_NAMES=('ground_10m.tif','surface_10m.tif','aoi_mask.tif','building_mask.tif','embankment_mask.tif','provenance.json','osm_water.geojson','buildings.json','landcover.bin')

def hazard_signature(signature):return [list(item) for item in signature if Path(item[0]).name in HAZARD_NAMES]

@lru_cache(maxsize=4)
def source_hashes(signature):
    return {item[0]:item[2] if item[0]=='response_context:scientific' else digest(ROOT/item[0]) for item in signature if item[1] is not None}

def response_scientific_signature(context):
    population=context.get('population',{})
    fields=('available','source','year','raster_path','unit','crs','origin_utm','width','height','coverage_fraction','valid_cell_count','nodata_cell_count','valid_population_total','expected_valid_population','conservation_error_float32')
    value={'stations':context.get('stations',[]),'facilities_status':context.get('facilities_status'),'population':{key:population.get(key) for key in fields}}
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    return ('response_context:scientific',len(raw),hashlib.sha256(raw).hexdigest())


def read(path,default=None):
    p=Path(path)
    if any('env' in x.lower() for x in p.parts) or p.is_symlink():raise ValueError('Protected or symlink path is not accepted')
    return json.loads(p.read_text()) if p.is_file() else default

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,allow_nan=False,separators=(',',':'))+'\n');temp.replace(path)

def population_path(path):
    p=Path(path);p=p if p.is_absolute() else ROOT/p
    if not p.is_relative_to(DERIVED) or '..' in p.parts or any('.env' in x.lower() or x.lower().endswith('.env') for x in p.parts) or p.is_symlink():raise ValueError('Population raster must be a regular derived-data file')
    return p

def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()

def url(path):return '/data/'+str(Path(path).relative_to(WEB_DATA))

def safe_id(value):
    if not isinstance(value,str) or not re.fullmatch(ID_PATTERN,value):raise ValueError('Invalid product run id')
    return value

def input_signature():
    paths=[DERIVED/name for name in ('ground_10m.tif','surface_10m.tif','aoi_mask.tif','building_mask.tif','embankment_mask.tif','provenance.json','osm_water.geojson')]+[WEB_DATA/name for name in ('buildings.json','landcover.bin','road_graph.json')]
    context=read(WEB_DATA/'response_context.json',{});population=context.get('population',{})
    if population.get('available') and population.get('raster_path'):
        p=Path(population['raster_path']);paths.append(population_path(p))
    return [(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),p.stat().st_size,p.stat().st_mtime_ns) if p.is_file() else (str(p.relative_to(ROOT)),None,None) for p in paths]+[response_scientific_signature(context)]

@lru_cache(maxsize=2)
def building_catalog(size,mtime):
    payload=read(WEB_DATA/'buildings.json');buildings=payload['buildings'];centers=np.array([np.mean(np.asarray(b['footprint'][0][:-1] or b['footprint'][0],float),axis=0) for b in buildings])
    return {'url':'/data/buildings.json','count':len(buildings),'sha256':digest(WEB_DATA/'buildings.json'),'order':'existing buildings array order','unique_entity_count':len({str(b.get('footprint_source'))+':'+str(b['id']) for b in buildings})},centers

def building_info():
    p=WEB_DATA/'buildings.json'
    if not p.is_file():return {'url':'/data/buildings.json','count':0,'sha256':None,'order':'existing buildings array order'},np.empty((0,2))
    stat=p.stat();return building_catalog(stat.st_size,stat.st_mtime_ns)

def shoreline_breach_selection(anchor):
    """Choose an actual shoreline cell with measured head and dry receiving land."""
    from sim.relative import river_reference
    manifest=read(RUNS/'relative_river_plus_3m'/'manifest.json',{})
    if not manifest.get('frames'):raise ValueError('Verified +3m baseline is required for riverbank selection')
    arrays={}
    for name in ('ground_10m.tif','surface_10m.tif','building_mask.tif','aoi_mask.tif'):
        if manifest.get('input_sha256',{}).get(name)!=digest(DERIVED/name):raise ValueError('Riverbank baseline terrain is stale')
        with rasterio.open(DERIVED/name) as source:arrays[name]=source.read(1)
    if manifest.get('input_sha256',{}).get('osm_water.geojson')!=digest(DERIVED/'osm_water.geojson'):raise ValueError('Riverbank baseline water outline is stale')
    ground=arrays['ground_10m.tif'];surface=arrays['surface_10m.tif'];valid=arrays['aoi_mask.tif']>0;blocked=arrays['building_mask.tif']>0
    water=(np.fromfile(WEB_DATA/'landcover.bin',dtype='uint8').reshape(ground.shape)&1)>0;river,reference,_=river_reference(ground,water,valid)
    filename=manifest['frames'][-1]['depth_file']
    if not re.fullmatch(r'depth_[A-Za-z0-9_-]+\.tif',filename):raise ValueError('Invalid baseline frame')
    path=RUNS/'relative_river_plus_3m'/filename
    with rasterio.open(path) as source:depth=source.read(1)
    lower=ndimage.minimum_filter(ground,7);eta=ndimage.maximum_filter(np.where(depth>.1,ground+depth,-np.inf),size=3)
    shore=ndimage.binary_dilation(river)&~river&valid&~blocked
    receiving=~river&valid&~blocked&(depth<=.1)&(surface<reference+3-.15)
    rr,cc=np.indices((9,9));kernel=((rr-4)**2+(cc-4)**2<=16).astype('int16');nearby=ndimage.convolve(receiving.astype('int16'),kernel,mode='constant')
    rr,cc=np.nonzero(shore&(depth<=.1)&(surface>reference+3+.1)&(eta-lower>.2)&(nearby>0))
    if not len(rr):raise ValueError('No mapped shoreline has both positive baseline head and nearby dry lower land')
    distances=np.hypot((cc+.5)*10-anchor[0],(rr+.5)*10-anchor[1]);chosen=int(np.argmin(distances));r,c=int(rr[chosen]),int(cc[chosen])
    if distances[chosen]>2500:raise ValueError('No suitable real shoreline candidate is within2.5km of the mapped ITO anchor')
    point=[(c+.5)*10,(r+.5)*10]
    return point,{'boundary_source':'Real OpenStreetMap river polygon shoreline, one-cell outside largest connected river component','label':'Hypothetical riverbank cut; engineered levee unconfirmed','selection':'Nearest unblocked dry shoreline cell to mapped ITO anchor with positive +3m wet-neighbour head and dry lower receiving terrain within40m','mapped_ito_anchor':anchor,'distance_to_anchor_m':float(distances[chosen]),'point':point,'baseline_run_id':'relative_river_plus_3m','baseline_time_s':manifest['frames'][-1]['elapsed_s'],'baseline_depth_sha256':digest(path),'original_surface_m':float(surface[r,c]),'proposed_local_minimum_m':float(lower[r,c]),'positive_head_m':float(eta[r,c]-lower[r,c]),'dry_receiving_cells_within40m':int(nearby[r,c]),'engineered_levee_confirmed':False,'previous_no_effect_run_id':'product_7e5a6a3715bca4cd'}

def presets():
    terrain=read(WEB_DATA/'terrain.json',{});info,centers=building_info();width=terrain.get('width',0)*terrain.get('cell_size_m',10);height=terrain.get('height',0)*terrain.get('cell_size_m',10)
    definitions=read(DERIVED/'what_if_scenarios.json',{}).get('scenarios',[]) or read(DERIVED/'relative_scenarios.json',{}).get('scenarios',[])
    breach=None
    for s in definitions:
        for edit in s.get('edits',[]):
            if edit.get('type')=='breach':breach=np.mean(edit['coordinates'],axis=0).tolist();break
        if breach is not None:break
    selection=None;breach_reason=None
    if breach is not None:
        try:breach,selection=shoreline_breach_selection(breach)
        except (ValueError,OSError) as error:breach=None;breach_reason=str(error)
    epicentre=[width*.68,height*.5]
    # Hypothetical ignition anchored to an actual footprint near mapped Chandni Chowk road.
    target=np.array([width*.25,height*.43]);anchor='AOI northwest urban footprint selection; market location not confirmed'
    roads=read(DERIVED/'osm_roads.geojson',{}).get('features',[])
    from pyproj import Transformer
    project=Transformer.from_crs(4326,terrain.get('crs','EPSG:32643'),always_xy=True)
    for feature in roads:
        if 'chandni' in str(feature.get('properties',{}).get('name','')).lower():
            coords=feature['geometry'].get('coordinates',[])
            if feature['geometry']['type']=='LineString' and coords:
                x,y=project.transform(*coords[len(coords)//2]);target=np.array([x-terrain['origin'][0],terrain['origin'][1]-y]);anchor='Nearest exported real building footprint to mapped OSM Chandni Chowk road';break
    fire_index=int(np.argmin(np.linalg.norm(centers-target,axis=1))) if len(centers) else None
    items=[{'id':'river_plus_3m','label':'River +3 m','scenario':{'module':'flood','river_plus_m':3}},
           {'id':'breach_near_ito','label':'ITO riverbank cut','scenario':{'module':'breach','point':breach,'river_plus_m':3,'bank_source':'osm_river_shoreline'},'location_note':'Hypothetical riverbank cut; engineered levee unconfirmed','selection':selection},
           {'id':'quake_m6_east_bank','label':'Earthquake M6 east bank','scenario':{'module':'quake','point':epicentre,'magnitude':6}},
           {'id':'old_delhi_market_fire','label':'Old Delhi market fire','scenario':{'module':'fire','building_index':fire_index,'wind_direction_deg':90,'wind_speed_m_s':3},'location_note':anchor},
           {'id':'chain_quake_flood','label':'Earthquake then river +3 m','scenario':{'module':'chain','point':epicentre,'magnitude':6,'river_plus_m':3}},
           {'id':'replay2023','label':'July 2023 replay','scenario':{'module':'flood','replay':True}}]
    from sim.replay import requirements
    replay_missing=requirements();replay=read(RUNS/'replay2023'/'manifest.json',{})
    if not replay.get('calibrated') or not replay.get('frames'):replay_missing.append('Completed calibrated static July 2023 replay')
    if not replay_missing:
        for name in ('ground_10m.tif','surface_10m.tif','aoi_mask.tif','building_mask.tif','embankment_mask.tif','s1_water_before.tif','s1_water_peak.tif','stage_2023.csv','datum_offset.json'):
            if replay.get('input_sha256',{}).get(name)!=digest(DERIVED/name):replay_missing.append('Static replay is stale: '+name)
        if any(not re.fullmatch(r'depth_[A-Za-z0-9_-]+\.tif',str(frame.get('depth_file',''))) or not (RUNS/'replay2023'/frame['depth_file']).is_file() for frame in replay.get('frames',[])):replay_missing.append('Static replay depth frames are incomplete')
    for item in items:
        missing=replay_missing if item['id']=='replay2023' else ['Real terrain/buildings are unavailable'] if not len(centers) else [breach_reason or 'Mapped ITO shoreline is unavailable'] if item['id']=='breach_near_ito' and breach is None else []
        item.update(enabled=not missing,reason='; '.join(missing) if missing else None);item['scenario']['preset_id']=item['id']
    return items

@lru_cache(maxsize=4)
def _preset_cache(key):return presets()

def replay_signature():
    paths=[DERIVED/name for name in ('s1_water_before.tif','s1_water_peak.tif','stage_2023.csv','datum_offset.json')]+[RUNS/'replay2023'/'manifest.json']
    return tuple((str(path),path.stat().st_mtime_ns,path.stat().st_size) if path.is_file() else (str(path),None,None) for path in paths)

def catalog():
    from sim.deployment import frozen_snapshot
    snapshot=frozen_snapshot()
    if snapshot:return snapshot['product_catalog']
    signature=input_signature();items=_preset_cache((tuple(signature),replay_signature()));info,_=building_info();index=read(INDEX,{'runs':[]})
    records=[r for r in index.get('runs',[]) if re.fullmatch(ID_PATTERN,str(r.get('run_id',''))) and (PUBLIC/r['run_id']/'manifest.json').is_file()]
    fingerprint=hashlib.sha256(json.dumps(signature).encode()).hexdigest()
    for record in records:
        prior=record.get('input_fingerprint')
        if prior is None:
            saved=read(PUBLIC/record['run_id']/'manifest.json',{}).get('input_signature',[])
            prior=hashlib.sha256(json.dumps(saved).encode()).hexdigest()
        record['source_current']=prior==fingerprint
    return {'schema_version':1,'label':LABEL,'coordinate_system':'local_metres_east_south','modules':[{'id':key,'label':value.labels['title'],'enabled':bool(info['count']),'missing':[] if info['count'] else ['real buildings'],'inputs':value.inputs,'labels':value.labels} for key,value in PLUGINS.items()],'presets':items,'building_index':info,'context_url':'/data/response_context.json','runs':records,'palette':PALETTE}

def normalize(request):
    if not isinstance(request,dict):raise ValueError('Scenario must be an object')
    allowed={'module','preset_id','river_plus_m','point','magnitude','building_index','wind_direction_deg','wind_speed_m_s','seed','replay','bank_source'}
    if set(request)-allowed:raise ValueError('Unsupported scenario fields')
    base={}
    if request.get('preset_id'):
        selected=next((p for p in catalog()['presets'] if p['id']==request['preset_id']),None)
        if not selected or not selected['enabled']:raise ValueError(selected.get('reason') if selected else 'Unknown preset')
        base.update(selected['scenario'])
    base.update(request);module=base.get('module','flood')
    if module not in PLUGINS:raise ValueError('Unknown disaster module')
    result={'module':module,'preset_id':base.get('preset_id'),'seed':base.get('seed',0),'replay':bool(base.get('replay',False))}
    if isinstance(result['seed'],bool) or not isinstance(result['seed'],int) or not 0<=result['seed']<=2147483647:raise ValueError('Seed must be a nonnegative 32-bit integer')
    if result['replay']:
        replay=next(p for p in catalog()['presets'] if p['id']=='replay2023')
        if module!='flood' or not replay['enabled']:raise ValueError(replay['reason'] or 'Replay is only a flood source')
    for key,default,low,high in [('river_plus_m',3,1,4),('magnitude',6,3,9),('wind_direction_deg',90,0,360),('wind_speed_m_s',3,0,30)]:
        value=float(base.get(key,default))
        if not np.isfinite(value) or not low<=value<=high:raise ValueError(f'{key} must be in [{low},{high}]')
        result[key]=value
    if result['replay']:result['river_plus_m']=0.
    if module=='breach':
        result['bank_source']=base.get('bank_source','mapped_embankment')
        if result['bank_source'] not in ('mapped_embankment','osm_river_shoreline'):raise ValueError('Unknown bank boundary source')
    if module in ('breach','quake','chain'):
        point=np.asarray(base.get('point',[]),float);terrain=read(WEB_DATA/'terrain.json',{});extent=terrain.get('extent_m',[0,0])
        if point.shape!=(2,) or not np.isfinite(point).all() or np.any(point<0) or np.any(point>=extent):raise ValueError('Choose a finite local-metre point inside the real AOI')
        result['point']=point.tolist()
    if module=='fire':
        value=base.get('building_index');count=building_info()[0]['count']
        if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<count:raise ValueError('Choose an actual exported building index')
        result['building_index']=value
    return result

class Context:
    def __init__(self):
        with rasterio.open(DERIVED/'ground_10m.tif') as source:self.ground=source.read(1);self.transform=source.transform;self.profile=source.profile;self.shape=source.shape;self.cell=abs(source.transform.a)
        self.origin=[self.transform.c,self.transform.f]
        def grid(name):
            with rasterio.open(DERIVED/name) as source:
                if source.shape!=self.shape or source.transform!=self.transform:raise ValueError('Misaligned source '+name)
                return source.read(1)
        self.surface=grid('surface_10m.tif');self.valid=grid('aoi_mask.tif').astype(bool);self.blocked=grid('building_mask.tif').astype(bool);self.banks=grid('embankment_mask.tif').astype(bool)
        cover=np.fromfile(WEB_DATA/'landcover.bin',dtype='uint8')
        if cover.size!=self.ground.size:raise ValueError('Real OSM landcover grid differs from terrain')
        self.water=(cover.reshape(self.shape)&1)>0
        self.buildings=read(WEB_DATA/'buildings.json')['buildings'];self.polygons=[Polygon(b['footprint'][0],b['footprint'][1:]) for b in self.buildings];self.centers=np.array([[p.centroid.x,p.centroid.y] for p in self.polygons]);self.heights=np.array([float(b['height']) if b.get('height') is not None else np.nan for b in self.buildings])
        keys=[str(b.get('footprint_source'))+':'+str(b['id']) for b in self.buildings];self.entity_keys,self.entities=np.unique(keys,return_inverse=True)
        rows=[];columns=[];owners=[]
        for i,building in enumerate(self.buildings):
            samples=[self.centers[i]]
            ring=np.asarray(building['footprint'][0],float)
            for a,b in zip(ring[:-1],ring[1:]):samples.extend(np.linspace(a,b,min(64,max(2,int(math.ceil(np.linalg.norm(b-a)/self.cell))+1))))
            xy=np.asarray(samples);rows.extend(np.clip((xy[:,1]/self.cell).astype(int),0,self.shape[0]-1));columns.extend(np.clip((xy[:,0]/self.cell).astype(int),0,self.shape[1]-1));owners.extend([i]*len(xy))
        self.sample_rows=np.asarray(rows,dtype='int32');self.sample_columns=np.asarray(columns,dtype='int32');self.sample_owner=np.asarray(owners,dtype='int32')
        self.response_context=read(WEB_DATA/'response_context.json',{});self.road_graph=read(WEB_DATA/'road_graph.json',{});self.population_meta=self.response_context.get('population',{'available':False,'reason':'WorldPop source is unavailable'});self.population=None
        population_path=self.population_meta.get('raster_path')
        if self.population_meta.get('available') and population_path:
            path=globals()['population_path'](population_path)
            with rasterio.open(path) as source:
                if source.shape!=self.shape or source.transform!=self.transform:raise ValueError('WorldPop counts grid differs from real terrain')
                self.population=source.read(1,masked=True).filled(np.nan)
    def building_max(self,grid):
        values=np.zeros(len(self.buildings),dtype=grid.dtype);np.maximum.at(values,self.sample_owner,grid[self.sample_rows,self.sample_columns]);return values
    def unique_count(self,mask):return int(len(np.unique(self.entities[np.asarray(mask,bool)])))
    def building_counts(self,levels):
        unique=np.zeros(len(self.entity_keys),dtype='uint8');np.maximum.at(unique,self.entities,levels)
        return [int(np.count_nonzero(unique==i)) for i in range(5)],int(np.count_nonzero(unique==255))
    def raster_buildings(self,indices,values,buffer_m=0):
        if not len(indices):return np.zeros(self.shape,dtype='float32')
        shapes=[(self.polygons[int(i)].buffer(buffer_m) if buffer_m else self.polygons[int(i)],float(value)) for i,value in sorted(zip(indices,values),key=lambda item:float(item[1]))]
        return rasterize(shapes,out_shape=self.shape,transform=Affine(self.cell,0,0,0,self.cell,0),all_touched=True,dtype='float32')
    def cached_frames(self,request):
        if request.get('replay'):run_id='replay2023'
        elif request['module']=='flood' and request['river_plus_m'] in (1,2,3):run_id=f"relative_river_plus_{int(request['river_plus_m'])}m"
        else:return self.cached_product_frames(request)
        manifest=read(RUNS/run_id/'manifest.json',{})
        if not manifest.get('frames'):raise ValueError('Required existing flood frames are unavailable: '+run_id)
        if request.get('replay') and not manifest.get('calibrated'):raise ValueError('Static replay is not calibrated')
        required=('ground_10m.tif','surface_10m.tif','building_mask.tif','aoi_mask.tif','embankment_mask.tif','osm_water.geojson') if not request.get('replay') else ('ground_10m.tif','surface_10m.tif','building_mask.tif','aoi_mask.tif','embankment_mask.tif','s1_water_before.tif','s1_water_peak.tif','stage_2023.csv','datum_offset.json')
        prior_metrics=read(RUNS/run_id/'metrics.json',{})
        if prior_metrics.get('water_balance',{}).get('relative_residual',0)>1e-8:raise ValueError('Cached flood failed water conservation')
        source_hashes=manifest.get('input_sha256',{})
        for name in required:
            if source_hashes.get(name)!=digest(DERIVED/name):raise ValueError('Cached flood source is stale or unverifiable: '+name)
        self.flood_source={'run_id':run_id,'configuration_hash':manifest.get('configuration_hash'),'input_sha256':source_hashes}
        result=[];first_time=None
        for i,frame in enumerate(manifest['frames']):
            name=frame.get('depth_file','')
            if '/' in name or not re.fullmatch(r'depth_[A-Za-z0-9_-]+\.tif',name):raise ValueError('Unsafe cached flood filename')
            path=RUNS/run_id/name
            if not path.is_file():raise ValueError('Cached flood frame is missing')
            elapsed=frame.get('elapsed_s')
            if elapsed is None:
                timestamp=frame.get('time_ist') or frame.get('time')
                if timestamp:
                    actual=datetime.fromisoformat(timestamp);first_time=first_time or actual;elapsed=(actual-first_time).total_seconds()
                else:raise ValueError('Replay frame lacks actual timestamp')
            result.append((float(elapsed),path))
        return result


    def cached_product_frames(self,request):
        # Population/facility refresh can reuse full-resolution native physics,
        # never a downsampled presentation grid or an unrelated scenario.
        index=read(INDEX,{'runs':[]});current=hazard_signature(input_signature())
        for record in reversed(index.get('runs',[])):
            run_id=record.get('run_id','')
            if not re.fullmatch(ID_PATTERN,str(run_id)):continue
            manifest=read(RUN_ROOT/run_id/'manifest.json',{})
            if manifest.get('scenario')!=request or hazard_signature(manifest.get('input_signature',[]))!=current:continue
            result=[];metadata={};offset=60 if request['module']=='chain' else 0
            for i,frame in enumerate(manifest.get('frames',[])):
                if not frame.get('depth_url'):continue
                path=RUN_ROOT/run_id/f'depth_{i:04d}.tif'
                if not path.is_file():result=[];break
                elapsed=float(frame['time_s'])-offset;result.append((elapsed,path))
                previous=read(PUBLIC/run_id/f'metrics_{i:04d}.json',{})
                if previous.get('water_balance'):metadata[elapsed]=previous['water_balance']
            if result:
                self.cached_water_balance=metadata;self.cached_terrain_edit=manifest.get('terrain_edit');self.flood_source={'run_id':run_id,'mode':'same-scenario full10m native depth reuse after response/population update','hazard_input_signature':current}
                return result
        return None

def reduced(values,stride=STRIDE,fill=0):
    height,width=values.shape;pad=np.pad(values,((0,(-height)%stride),(0,(-width)%stride)),constant_values=fill)
    return pad.reshape(math.ceil(height/stride),stride,math.ceil(width/stride),stride).max(axis=(1,3))

def impact(ctx,frame,response,run_id):
    land=ctx.valid&~ctx.water
    satellite=water_overlap(frame.depth,ctx.valid,getattr(ctx,'profile',{}))
    affected=land&((frame.depth>.1) if frame.depth is not None else (frame.severity>0)&(frame.severity!=255))
    if frame.debris is not None:affected|=land&(frame.debris>.05)
    if frame.extra.get('module')=='chain':affected|=land&(frame.severity>0)&(frame.severity!=255)
    bands=[{'band':i,'cells':int(np.count_nonzero(land&(frame.severity==i))),'area_km2':float(np.count_nonzero(land&(frame.severity==i))*ctx.cell**2/1e6)} for i in range(5)]
    buildings,unknown=ctx.building_counts(frame.building_severity);people=None;known=None;coverage=None
    if ctx.population is not None:
        covered=affected&np.isfinite(ctx.population);coverage=float(covered.sum()/affected.sum()) if affected.any() else 1.;known=float(np.sum(ctx.population[covered],dtype='float64'));people=known if coverage>=1-1e-12 else None
    return {**satellite,'run_id':run_id,'module':frame.extra.get('module'),'time_s':frame.time_s,'label':LABEL,'affected_area_km2':float(affected.sum()*ctx.cell**2/1e6),'ground_bands':bands,'buildings_per_band':buildings,'building_parts_per_band':[int(np.count_nonzero(frame.building_severity==i)) for i in range(5)],'buildings_unknown':unknown,'roads_closed':response['metrics']['roads_closed'],'people_affected':people,'people_in_known_cells':known,'affected_population_coverage':coverage,'population':ctx.population_meta,'response':response['metrics'],'definitions':{'area':'Saved-frame affected land, excluding mapped normal OSM water; flood depth >0.1m, shaking/fire nonzero or debris.','building_exposure':'Maximum depth within one cell of real footprint centroid and boundary samples at <=10m spacing (64 samples per edge cap); exterior exposure, not indoor depth.','building_count':'Unique footprint-source/id entities; part arrays remain in exact exported order.','population':'WorldPop2020 residential population counts; not live occupancy, casualties or actual evacuees.','roads':'Directed graph hazards assessed on geometry sampled every <=10m; physical segment IDs deduplicated.'},'limitations':response['limitations'],**frame.extra}

def export_metrics(run_id):
    """Refresh only overlap fields from saved native depths; no physics rerun."""
    safe_id(run_id);manifest=read(PUBLIC/run_id/'manifest.json')
    if not manifest:raise ValueError('Published run is unavailable')
    with rasterio.open(DERIVED/'aoi_mask.tif') as source:
        valid=source.read(1)>0;profile=source.profile.copy()
    source_run=manifest.get('flood_source') or {};source_id=source_run.get('run_id','')
    native=[]
    if re.fullmatch(r'(?:product_[a-f0-9]{16}|relative_[a-z0-9_]+|replay2023)',source_id):
        source_manifest=read((RUN_ROOT if source_id.startswith('product_') else RUNS)/source_id/'manifest.json',{})
        native=source_manifest.get('frames',[])
    scores=[]
    for index,frame in enumerate(manifest['frames']):
        path=RUN_ROOT/run_id/f'depth_{index:04d}.tif';depth=None
        if frame.get('depth_url') and not path.is_file() and index<len(native):
            name=native[index].get('depth_file',f'depth_{index:04d}.tif')
            if not re.fullmatch(r'depth_[A-Za-z0-9_-]+\.tif',name):raise ValueError('Invalid native depth filename')
            path=(RUN_ROOT if source_id.startswith('product_') else RUNS)/source_id/name
        if frame.get('depth_url'):
            if not path.is_file():raise ValueError('Native depth frame unavailable; presentation grids cannot be used for scoring')
            with rasterio.open(path) as source:
                if source.shape!=valid.shape or source.transform!=profile['transform'] or source.crs!=profile['crs']:raise ValueError('Native depth grid differs from the AOI')
                depth=source.read(1)
        score=water_overlap(depth,valid,profile);scores.append(score)
    # Prepare every score before writing so a missing native frame cannot leave a partial update.
    for index,score in enumerate(scores):
        path=PUBLIC/run_id/f'metrics_{index:04d}.json';values=read(path);values.update(score);write(path,values)
    summary=read(PUBLIC/run_id/'metrics.json');summary.update(scores[-1]);summary['sentinel_score_frame_index']=len(scores)-1
    write(PUBLIC/run_id/'metrics.json',summary);write(RUN_ROOT/run_id/'metrics.json',summary)
    return {'run_id':run_id,**scores[-1],'sentinel_score_frame_index':len(scores)-1}


def request_record(request):
    normalized=normalize(request);signature=input_signature();key={'version':VERSION,'request':normalized,'input_signature':signature}
    if normalized.get('preset_id')=='breach_near_ito':key['selection_provenance']=next(p['selection'] for p in catalog()['presets'] if p['id']=='breach_near_ito')
    run_id='product_'+hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:16]
    return run_id,key

def status(run_id):
    from sim.deployment import frozen_snapshot
    if frozen_snapshot():
        safe_id(run_id)
        if complete_manifest(run_id):return {'status':'complete','run_id':run_id,'manifest_url':url(PUBLIC/run_id/'manifest.json'),'status_url':f'/product/runs/{run_id}/status'}
    safe_id(run_id);record=read(RUN_ROOT/run_id/'status.json',{'status':'unavailable','reason':'No admitted product run'})
    return {**record,'run_id':run_id,'manifest_url':url(PUBLIC/run_id/'manifest.json'),'status_url':f'/product/runs/{run_id}/status'}

def record_status(run_id,state,**extra):write(RUN_ROOT/run_id/'status.json',{'run_id':run_id,'status':state,'updated_at':datetime.now(timezone.utc).isoformat(),**extra})

def publish_index(record):
    with jobs.serialized('product_index'):
        index=read(INDEX,{'schema_version':1,'runs':[]});index['runs']=[r for r in index['runs'] if r['run_id']!=record['run_id']]+[record];write(INDEX,index)
        write(WEB_DATA/'product'/'catalog.json',catalog())

def breach_comparison(ctx,depth,scenario):
    rise=scenario['river_plus_m']
    if rise not in (1,2,3):return {'available':False,'reason':'No matching native baseline for this stage'}
    run_id=f'relative_river_plus_{int(rise)}m';manifest=read(RUNS/run_id/'manifest.json',{})
    for name in ('ground_10m.tif','surface_10m.tif','building_mask.tif','aoi_mask.tif','embankment_mask.tif','osm_water.geojson'):
        if manifest.get('input_sha256',{}).get(name)!=digest(DERIVED/name):return {'available':False,'reason':'Matched baseline inputs are stale'}
    frame=manifest['frames'][-1];name=frame['depth_file']
    if not re.fullmatch(r'depth_[A-Za-z0-9_-]+\.tif',name):raise ValueError('Unsafe baseline depth filename')
    with rasterio.open(RUNS/run_id/name) as source:
        if source.shape!=ctx.shape or source.transform!=ctx.transform:raise ValueError('Matched baseline grid differs')
        baseline=source.read(1)
    difference=depth-baseline;land=ctx.valid&~ctx.water
    return {'available':True,'baseline_run_id':run_id,'matched_river_plus_m':rise,'time_s':frame['elapsed_s'],'changed_cells_gt_1mm':int(np.count_nonzero(np.abs(difference)>.001)),'maximum_abs_depth_difference_m':float(np.abs(difference).max()),'baseline_affected_area_km2':float(np.count_nonzero(land&(baseline>.1))*ctx.cell**2/1e6),'cut_affected_area_km2':float(np.count_nonzero(land&(depth>.1))*ctx.cell**2/1e6),'affected_area_difference_km2':float((np.count_nonzero(land&(depth>.1))-np.count_nonzero(land&(baseline>.1)))*ctx.cell**2/1e6),'signed_above_original_ground_water_volume_difference_m3':float(difference.sum(dtype='float64')*ctx.cell**2),'note':'Same10m terrain, mapped-water reference, +stage forcing, fixed0.25s step and300s duration; compares depths above original ground, not excavated-channel storage.'}

def complete_manifest(run_id):
    manifest=read(PUBLIC/run_id/'manifest.json',{})
    if not manifest.get('frames'):return None
    prefix=url(PUBLIC/run_id)+'/'
    for record in [manifest,*manifest['frames']]:
        for key,value in record.items():
            if key.endswith('_url'):
                if not isinstance(value,str) or not value.startswith(prefix) or '/' in value[len(prefix):] or not (PUBLIC/run_id/value[len(prefix):]).is_file():return None
    grid=manifest.get('grid',{});cells=grid.get('width',0)*grid.get('height',0);count=manifest.get('building_index',{}).get('count',0)
    if not cells or not count:return None
    for frame in manifest['frames']:
        for key,size in (('severity_url',cells),('building_states_url',count*2),('depth_url',cells*4)):
            if key in frame and (PUBLIC/run_id/frame[key][len(prefix):]).stat().st_size!=size:return None
    return manifest

def compute(run_id,job_name=None):
    safe_id(run_id)
    with jobs.serialized('product_compute'):
        return _compute(run_id,job_name)

def _compute(run_id,job_name=None):
    safe_id(run_id);started=time.perf_counter();directory=RUN_ROOT/run_id;request=read(directory/'request.json');scenario=request['request'];public=PUBLIC/run_id;job_name=job_name or run_id
    complete=complete_manifest(run_id)
    if complete:return complete
    record_status(run_id,'running',stage='loading_real_inputs',job_name=job_name);jobs.update(job_name,f'{run_id}: loading real terrain, building geometry and response context',status='running')
    try:
        if json.loads(json.dumps(input_signature()))!=request['input_signature']:raise ValueError('Real input snapshot changed while queued; submit scenario again')
        actual_hashes=source_hashes(tuple(tuple(item) for item in request['input_signature']))
        ctx=Context()
        if json.loads(json.dumps(input_signature()))!=request['input_signature']:raise ValueError('Real inputs changed while loading; submit scenario again')
        planner=Planner(ctx.response_context,ctx.road_graph,ctx.shape,ctx.cell,ctx.valid,ctx.water);plugin=PLUGINS[scenario['module']]().apply(ctx,scenario);info=building_info()[0]
        public.mkdir(parents=True,exist_ok=True);frames=[];metrics=[];worst=np.zeros(len(ctx.buildings),dtype='uint8');ground_worst=np.zeros(ctx.shape,dtype='uint8');last_response=None
        for i,frame in enumerate(plugin.frames()):
            if frame.depth is not None and (not np.isfinite(frame.depth).all() or np.any(frame.depth<0)):raise ValueError('Model produced invalid water depth')
            if frame.severity.shape!=ctx.shape or len(frame.building_state)!=len(ctx.buildings):raise ValueError('Model output differs from source grid/building index')
            if frame.extra.get('water_balance',{}).get('relative_residual',0)>1e-8:raise FloatingPointError('Water balance failed; completed run will not be published')
            frame.extra['module']=scenario['module'];response=planner.frame(frame,ctx.population);values=impact(ctx,frame,response,run_id);values['label']='Simulated July 2023 replay' if scenario.get('replay') else LABEL
            paths={name:public/f'{name}_{i:04d}.{extension}' for name,extension in [('severity','bin'),('building_states','bin'),('metrics','json'),('response','json'),('depth','bin')]}
            severity=reduced(np.where(frame.severity==255,0,frame.severity));valid=reduced(ctx.valid.astype('uint8'));severity[valid==0]=255;severity.astype('uint8').tofile(paths['severity'])
            states=np.column_stack((frame.building_severity,frame.building_state)).astype('uint8');states.tofile(paths['building_states']);write(paths['metrics'],values);write(paths['response'],response)
            entry={'time_s':frame.time_s,**{key+'_url':url(paths[key]) for key in ('severity','building_states','metrics','response')}}
            if frame.depth is not None:
                reduced(frame.depth).astype('<f4').tofile(paths['depth']);entry['depth_url']=url(paths['depth'])
                if not getattr(plugin,'cached',None):
                    raw=directory/f'depth_{i:04d}.tif';profile=ctx.profile.copy();profile.update(dtype='float32',nodata=0,compress='deflate')
                    with rasterio.open(raw,'w',**profile) as output:output.write(frame.depth.astype('float32'),1)
            frames.append(entry);metrics.append(values);last_response=response;np.maximum(worst,frame.building_severity,out=worst);np.maximum(ground_worst,frame.severity,out=ground_worst)
            record_status(run_id,'running',stage='impact_response_export',frames_completed=len(frames),time_s=frame.time_s,job_name=job_name);jobs.update(job_name,f'{run_id}: computed frame {i+1}, simulated {frame.time_s:g}s; {time.perf_counter()-started:.1f}s elapsed',status='running')
        counts,unknown=ctx.building_counts(worst);summary=dict(metrics[-1]);summary.update(time_s=None,frames=len(frames),runtime_s=time.perf_counter()-started,affected_area_km2=max(m['affected_area_km2'] for m in metrics),buildings_per_band=counts,buildings_unknown=unknown,roads_closed=max((m['roads_closed'] for m in metrics if m['roads_closed'] is not None),default=None),people_affected=max((m['people_affected'] for m in metrics),default=None) if all(m['people_affected'] is not None for m in metrics) else None,summary_definition='Maximum saved-frame land area/people/road closures; each building counted at its highest saved-frame severity. Response values are the final saved frame.')
        if scenario['module']=='breach':summary['counterfactual_comparison']=breach_comparison(ctx,frame.depth,scenario)
        write(public/'metrics.json',summary);write(public/'response.json',last_response)
        manifest={'schema_version':1,'run_id':run_id,'preset_id':scenario.get('preset_id'),'module':scenario['module'],'label':summary['label'],'hypothetical':not scenario.get('replay'),'calibrated':bool(scenario.get('replay')),'created_at':datetime.now(timezone.utc).isoformat(),'grid':{'width':math.ceil(ctx.shape[1]/STRIDE),'height':math.ceil(ctx.shape[0]/STRIDE),'cell_size_m':ctx.cell*STRIDE,'source_cell_size_m':ctx.cell,'stride':STRIDE,'origin_utm':ctx.origin,'order':'row-major north-to-south','depth_encoding':'Float32 little-endian','severity_encoding':'Uint8,0..4,255 unknown'},'building_index':info,'building_states':{'encoding':'Uint8 interleaved [severity,state]','stride':2,'count':len(ctx.buildings),'state_codes':STATE_CODES},'frames':frames,'metrics_url':url(public/'metrics.json'),'response_url':url(public/'response.json'),'labels':plugin.labels,'palette':PALETTE,'scenario':scenario,'input_signature':request['input_signature'],'source_sha256':actual_hashes,'population':ctx.population_meta,'facilities_status':ctx.response_context.get('facilities_status'),'limitations':[*plugin.labels['limitations'],*last_response['limitations']],'resource_policy':{'worker_limit':2,'nice':15},'runtime_s':time.perf_counter()-started,'flood_source':getattr(ctx,'flood_source',None),'source_mode':'existing verified full-resolution flood frames' if getattr(ctx,'flood_source',None) else 'fresh hypothetical model'}
        manifest.update(implementation_version=VERSION,selection_provenance=request.get('selection_provenance'),terrain_edit=getattr(plugin,'breach_info',None),freshness_scope='Scientific station records, population coverage/counts and aligned raster identity; acquisition checksum completion and generated timestamps do not invalidate runs.')
        write(public/'manifest.json',manifest);write(directory/'manifest.json',manifest);write(directory/'metrics.json',summary);record_status(run_id,'complete',stage='complete',frames_completed=len(frames),runtime_s=manifest['runtime_s'],job_name=job_name)
        publish_index({'run_id':run_id,'preset_id':scenario.get('preset_id'),'module':scenario['module'],'manifest_url':url(public/'manifest.json'),'label':manifest['label'],'created_at':manifest['created_at'],'input_fingerprint':hashlib.sha256(json.dumps(request['input_signature']).encode()).hexdigest()});return manifest
    except Exception as error:
        record_status(run_id,'failed',reason=str(error),job_name=job_name);raise

class LocalComputeDisabled(RuntimeError):
    pass

def admit(request,launch=True):
    from sim.deployment import frozen_snapshot
    snapshot=frozen_snapshot()
    if snapshot:
        normalized=normalize(request)
        for record in reversed(snapshot['product_catalog']['runs']):
            if not record.get('source_current'):continue
            candidate=read(PUBLIC/record['run_id']/'manifest.json',{}).get('scenario',{})
            if all(candidate.get(key)==value for key,value in normalized.items() if key!='preset_id'):
                return status(record['run_id'])
        raise LocalComputeDisabled('This deployment serves verified precomputed scenarios. New calculations require the operator model worker.')
    run_id,record=request_record(request);directory=RUN_ROOT/run_id
    with jobs.serialized('product_admission'):
        existing=status(run_id)
        if existing['status']=='complete' and complete_manifest(run_id):return existing
        if jobs.active(run_id) or (existing.get('job_name') and existing['status'] in ('queued','running') and jobs.active(existing['job_name'])):return existing
        if launch and os.environ.get('OTEC_ENABLE_LOCAL_COMPUTE') != '1':
            raise LocalComputeDisabled('New simulations require the model server. Cached scenarios remain available; local background computation is off.')
        write(directory/'request.json',record);record_status(run_id,'queued',job_name=run_id)
        if launch:
            import sys
            jobs.launch(run_id,['/usr/bin/nice','-n','15',sys.executable,'-B','-m','sim.product','run',run_id],detail='Preparing real-data product scenario',force=True)
    return status(run_id)

def precompute(job_name='product_precompute'):
    write(WEB_DATA/'product'/'catalog.json',catalog())
    scenarios=[{'preset_id':name} for name in ('quake_m6_east_bank','chain_quake_flood','river_plus_3m','old_delhi_market_fire','breach_near_ito')]+[{'module':'flood','river_plus_m':rise} for rise in (1,2,4)]
    for scenario in scenarios:
        for attempt in range(2):
            run_id,record=request_record(scenario)
            with jobs.serialized('product_admission'):
                write(RUN_ROOT/run_id/'request.json',record)
                if not complete_manifest(run_id):record_status(run_id,'queued',job_name=job_name)
            try:compute(run_id,job_name=job_name);break
            except ValueError as error:
                if attempt or 'input' not in str(error).lower() or 'changed while' not in str(error):raise
    write(WEB_DATA/'product'/'catalog.json',catalog())

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['catalog','run','precompute','refresh-population']);parser.add_argument('run_id',nargs='?');args=parser.parse_args()
    if args.command=='catalog':
        value=catalog();write(WEB_DATA/'product'/'catalog.json',value);print(json.dumps(value,indent=2));return
    if os.getpriority(os.PRIO_PROCESS,0)<15:os.setpriority(os.PRIO_PROCESS,0,15)
    if args.command=='run':compute(args.run_id);return
    if args.command=='refresh-population':
        jobs.update('product_population_refresh','Waiting for the existing real WorldPop download; no population fallback',status='running')
        while not read(WEB_DATA/'response_context.json',{}).get('population',{}).get('available'):
            state=jobs.state('response_population') or {}
            if state.get('status') in ('failed','blocked','needs_input'):
                jobs.update('product_population_refresh','WorldPop source job needs attention; no population values invented',status='needs_input');raise SystemExit(1)
            time.sleep(15)
        jobs.update('product_population_refresh','Real population is ready; waiting for the shared compute lock before refreshing scenarios',status='running')
        precompute('product_population_refresh');return
    precompute()
if __name__=='__main__':main()
