"""OSM/FABDEM relative-stage experiments: hypothetical, not a forecast.

This deliberately does not use Sentinel-1 or gauge calibration and never scores
itself against satellite observations. It reuses deterministic virtual pipes.
"""
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import platform
import re
import time

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy import ndimage
from pyproj import Transformer
from shapely.geometry import shape

from sim.config import CELL_SIZE,CRS,DERIVED,RUNS
from sim.data import write_json
from sim.dynamic import DynamicConfig,VirtualPipes,_apply_edits,_exposure,scenario_definitions
from sim.geometry import transform_xy
from sim.export_web import read_json,export_all
from sim.replay import main_river
from sim.jobs import update

LABEL='Hypothetical, not a forecast'
MODEL='relative-stage virtual-pipe local-inertial shallow water'
VERSION=1
CONFIG=DynamicConfig(dt_s=.25,duration_s=300,output_interval_s=10,river_ramp_s=60)
RUN_IDS=('relative_river_plus_1m','relative_river_plus_2m','relative_river_plus_3m','relative_breach_near_ito','relative_debris_ring_road')

def input_paths():
    names=['ground_10m.tif','surface_10m.tif','building_mask.tif','embankment_mask.tif','aoi_mask.tif','osm_water.geojson','provenance.json']
    buildings='buildings_merged.geojson' if (DERIVED/'buildings_merged.geojson').is_file() else 'osm_buildings.geojson'
    names.extend(name for name in (buildings,'osm_roads.geojson') if (DERIVED/name).is_file())
    return [DERIVED/name for name in names]

def input_signature():
    return [(str(path),path.stat().st_mtime_ns,path.stat().st_size) for path in input_paths()]

def cache_complete(directory,digest):
    manifest=read_json(directory/'manifest.json',{})
    if not digest or manifest.get('configuration_hash')!=digest or not (directory/'metrics.json').is_file():return False
    frames=manifest.get('frames',[])
    return bool(frames) and all(re.fullmatch(r'depth_[0-9]+_[0-9]+\.tif',str(frame.get('depth_file',''))) and (directory/frame['depth_file']).is_file() for frame in frames)

def requirements():
    names=('ground_10m.tif','surface_10m.tif','building_mask.tif','embankment_mask.tif','aoi_mask.tif','osm_water.geojson')
    missing=[n for n in names if not (DERIVED/n).is_file()]
    if 'FABDEM' not in str(read_json(DERIVED/'provenance.json',{}).get('terrain',{}).get('source','')).upper():missing.append('FABDEM provenance')
    return missing

def river_reference(ground,water,valid,cell_size=CELL_SIZE):
    river=main_river(water&valid)
    medians=np.full(ground.shape[0],np.nan)
    for row in range(ground.shape[0]):
        samples=ground[row,river[row]]
        if len(samples):medians[row]=np.median(samples)
    known=np.flatnonzero(np.isfinite(medians))
    if len(known)<2:raise ValueError('Mapped river needs at least two observed cross-sections')
    profile=np.interp(np.arange(len(medians)),known,medians[known])
    width=max(3,round(500/cell_size));width+=1-width%2
    profile=ndimage.median_filter(profile,size=width,mode='nearest')
    reference=np.broadcast_to(profile[:,None],ground.shape).copy()
    return river,reference,{'method':'Per-grid-row median FABDEM elevation inside largest connected mapped OSM water polygon component; interpolate gaps, then 500 m longitudinal median filter.','profile_m':profile.tolist(),'smoothing_m':width*cell_size,'min_reference_m':float(profile.min()),'max_reference_m':float(profile.max()),'datum':'FABDEM EGM2008, not gauge datum','caveat':'Mapped-water surface proxy derived from FABDEM; no surveyed bathymetry or observed hydrograph. Initial sub-cell relief creates model storage only, not measured river depths.'}

class RelativePipes(VirtualPipes):
    def __init__(self,*args,reference,**kwargs):
        super().__init__(*args,**kwargs)
        if reference.shape!=self.bed.shape or not np.isfinite(reference[self.boundary]).all():raise ValueError('Invalid mapped river reference')
        self.reference=reference[self.boundary]
    def impose_stage(self,rise_m):
        if not np.isfinite(rise_m):raise ValueError('Relative rise must be finite')
        desired=np.maximum(0,self.reference+rise_m-self.bed[self.boundary])
        delta=(desired-self.depth[self.boundary])*self.config.cell_size_m**2
        self.boundary_added_m3+=float(delta[delta>0].sum());self.boundary_removed_m3+=float(-delta[delta<0].sum())
        self.depth[self.boundary]=desired

def definitions():
    mapped=scenario_definitions()['scenarios']
    return [{'run_id':f'relative_river_plus_{m}m','river_plus_m':m,'edits':[],'label':f'River +{m} m','assumption':'Hypothetical rise above the FABDEM-derived mapped-river surface.'} for m in (1,2,3)]+[
      {'run_id':'relative_breach_near_ito','river_plus_m':1,'edits':mapped[0]['edits'],'source_feature':mapped[0]['source_feature'],'label':'Breach near ITO · river +1 m','assumption':mapped[0]['assumption']+' Common +1 m forcing makes the edit comparable to the unedited +1 m experiment.'},
      {'run_id':'relative_debris_ring_road','river_plus_m':1,'edits':mapped[1]['edits'],'source_feature':mapped[1]['source_feature'],'label':'Debris on Ring Road · river +1 m','assumption':mapped[1]['assumption']+' Common +1 m forcing makes the edit comparable to the unedited +1 m experiment.'}]

def compute(run_id,edits=None,river_plus_m=1,config=None,job_name='relative_scenarios'):
    if run_id not in RUN_IDS and not re.fullmatch(r'relative_edit_[a-f0-9]{16}',run_id):raise ValueError('Invalid relative run id')
    missing=requirements()
    if missing:raise ValueError('Missing relative-stage inputs: '+', '.join(missing))
    if not np.isfinite(river_plus_m) or not 0<=river_plus_m<=3:raise ValueError('Relative river rise must be 0–3 m')
    cfg=config or CONFIG;cfg.validate();started=time.perf_counter()
    inputs={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in input_paths()}
    specification={'version':VERSION,'inputs':inputs,'edits':edits or [],'river_plus_m':river_plus_m,'config':asdict(cfg)}
    digest=hashlib.sha256(json.dumps(specification,sort_keys=True).encode()).hexdigest();directory=RUNS/run_id
    if cache_complete(directory,digest):return run_id
    with rasterio.open(DERIVED/'ground_10m.tif') as ref:
        ground=ref.read(1);profile=ref.profile.copy()
        def aligned(name):
            with rasterio.open(DERIVED/name) as src:
                if src.shape!=ref.shape or src.crs!=ref.crs or src.transform!=ref.transform:raise ValueError('Misaligned input '+name)
                return src.read(1)
        valid=aligned('aoi_mask.tif').astype(bool);blocked=aligned('building_mask.tif').astype(bool);banks=aligned('embankment_mask.tif').astype(bool);surface=aligned('surface_10m.tif')
        project=Transformer.from_crs(4326,CRS,always_xy=True).transform
        water=rasterize([(transform_xy(project,shape(f['geometry'])),1) for f in read_json(DERIVED/'osm_water.geojson')['features']],out_shape=ground.shape,transform=ref.transform,dtype='uint8').astype(bool)
        river,reference,reference_meta=river_reference(ground,water,valid)
        bed,changed=_apply_edits(surface,ground,banks,valid,ref.transform,edits or [])
        reservoir=river&valid&~blocked&~changed
        if not reservoir.any():raise ValueError('No unblocked mapped river cells remain for the relative-stage boundary')
        initial=np.where(river&valid&~blocked,np.maximum(0,reference-surface),0)
        solver=RelativePipes(bed,initial,valid,blocked,reservoir,cfg,reference=reference)
        solver.impose_stage(0)
        directory.mkdir(parents=True,exist_ok=True);profile.update(dtype='float32',count=1,nodata=0,compress='deflate')
        frames=[];maximum=np.zeros_like(ground,dtype='float32');revision=str(int(digest[:12],16));write_s=0
        def save(step):
            nonlocal write_s
            t=time.perf_counter();elapsed=step*cfg.dt_s;rise=river_plus_m*min(1,elapsed/cfg.river_ramp_s)
            depth=np.where(solver.depth>1e-6,np.maximum(0,solver.bed+solver.depth-ground),0).astype('float32')
            np.maximum(maximum,depth,out=maximum)
            name=f'depth_{step:07d}_{revision}.tif';temp=directory/(name+'.tmp')
            with rasterio.open(temp,'w',**profile) as dst:dst.write(depth,1)
            temp.replace(directory/name)
            frames.append({'elapsed_s':elapsed,'level_m':rise,'relative_stage_m':rise,'interpolated':False,'stage_kind':LABEL+' · relative to FABDEM/OSM reference','depth_file':name,'overland_area_km2':float(np.count_nonzero((depth>.1)&~water&valid)*CELL_SIZE**2/1e6)})
            write_s+=time.perf_counter()-t
        save(0);steps=round(cfg.duration_s/cfg.dt_s);output_steps=round(cfg.output_interval_s/cfg.dt_s)
        for step in range(1,steps+1):
            solver.step(river_plus_m*min(1,step*cfg.dt_s/cfg.river_ramp_s))
            if step%output_steps==0 or step==steps:
                save(step);update(job_name,f'{run_id}: {step}/{steps} steps; simulated {step*cfg.dt_s:g}s; {time.perf_counter()-started:.1f}s wall time')
        balance=solver.water_balance()
        if balance['relative_residual']>1e-8:raise FloatingPointError('Water volume balance failed')
        update(job_name,f'{run_id}: computing real mapped exposure counts')
        exposure=_exposure(maximum,ref)
        metrics={'model':MODEL,'label':LABEL,'hypothetical':True,'calibrated':False,'iou':None,'precision':None,'recall':None,'flooded_area_km2':float(np.count_nonzero((maximum>.1)&~water&valid)*CELL_SIZE**2/1e6),'wet_area_including_river_km2':float(np.count_nonzero(maximum>.1)*CELL_SIZE**2/1e6),**exposure,'water_balance':balance,'runtime_s':time.perf_counter()-started,'output_write_wall_time_s':write_s,'runtime_host':{'system':platform.system(),'machine':platform.machine(),'python':platform.python_version()},'relative_stage_m':river_plus_m,'parameters':asdict(cfg),'area_definition':'Maximum saved-frame depth >0.1 m outside all mapped OSM water polygons; hypothetical overland wetting.','buildings_wet_definition':'Each mapped footprint counted once if any touched cell or its immediate 8-neighbour cells exceeds 0.5 m maximum saved-frame depth. Buildings are impermeable; this is nearby exposure, not simulated indoor depth.','roads_closed_definition':'Mapped road segments exposed to >0.3 m simulated depth; not observed closures.','score_caveat':'No satellite score: an explicitly hypothetical OSM/FABDEM-relative experiment, not the July 2023 replay.'}
        write_json(directory/'metrics.json',metrics)
        write_json(directory/'manifest.json',{'run_id':run_id,'model':MODEL,'hypothetical':True,'label':LABEL,'calibrated':False,'frames':frames,'relative_stage_m':river_plus_m,'edits':edits or [],'configuration_hash':digest,'input_sha256':inputs,'parameters':asdict(cfg),'river_reference':reference_meta,'created_at':datetime.now(timezone.utc).isoformat(),'limitations':'Local-inertial approximation; no bathymetry, observed inflow, rainfall, drainage or calibrated roughness. Closed AOI boundaries. Do not use for forecasts.'})
    export_all();print(json.dumps({'run_id':run_id,'runtime_s':metrics['runtime_s'],'flooded_area_km2':metrics['flooded_area_km2']}),flush=True)
    return run_id

def precompute():
    scenarios=definitions();write_json(DERIVED/'relative_scenarios.json',{'label':LABEL,'scenarios':scenarios,'parameters':asdict(CONFIG),'decided_by':'User requested relative stage; Codex uses equal +1 m forcing for breach/debris comparisons.'})
    for scenario in scenarios:compute(scenario['run_id'],scenario['edits'],scenario['river_plus_m'])
    update('relative_scenarios','All five hypothetical relative-stage runs, metrics and web exports complete')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('precompute','check','edit'));parser.add_argument('run_id',nargs='?');args=parser.parse_args()
    if args.command=='check':print(json.dumps({'missing':requirements()},indent=2))
    elif args.command=='edit':
        if not args.run_id or not re.fullmatch(r'relative_edit_[a-f0-9]{16}',args.run_id):parser.error('Invalid edit run')
        request=read_json(RUNS/args.run_id/'request.json')
        compute(args.run_id,request['edits'],request['river_plus_m'],job_name='scenario_'+args.run_id.removeprefix('relative_edit_'))
    else:precompute()
