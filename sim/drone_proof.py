"""Fresh drone reconstruction and independent LiDAR validation; no fallback media.

CLI: python -m sim.drone_proof status|frames|sparse|align|dense|rasterize|validate
     --manifest data/raw/drone_proof/inputs.json [--run RUN_DIRECTORY]

COLMAP is used for CPU sparse SfM. OpenMVS provides CPU dense reconstruction on
the Mac; native COLMAP PatchMatch cannot run without a supported GPU backend.
Only a new run created by `frames` is accepted. Old dense products are never
adopted. Source acquisition dates and the reconstruction date remain separate.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from rasterio.warp import reproject, Resampling
from scipy import ndimage
from scipy.spatial.transform import Rotation
from shapely.geometry import Point, shape, mapping
from shapely.ops import transform

ROOT = Path(__file__).absolute().parents[1]
BASE = ROOT / 'data' / 'derived' / 'drone_proof'
MANIFEST = ROOT / 'data' / 'raw' / 'drone_proof' / 'inputs.json'
METRICS = ROOT / 'runs' / 'drone_proof' / 'metrics.json'
LABEL = 'Real drone images (OpenDroneMap aukerman) and real USGS LiDAR. Delhi drone data is not public.'
TOOLS = ('ffmpeg', 'ffprobe', 'colmap', 'InterfaceCOLMAP', 'DensifyPointCloud', 'pdal')
STAGES = ('frames', 'features', 'sparse', 'gps_alignment', 'dense', 'ndsm', 'lidar', 'validation')


def safe_path(value, base=ROOT):
    """Reject environment-like names and all symlinks before opening inputs."""
    p = Path(value)
    if not p.is_absolute(): p = base / p
    p = p.absolute()
    if '..' in p.parts: raise ValueError('Parent traversal is not accepted')
    if any('env' in part.lower() for part in p.parts):
        raise ValueError('Environment-like paths are inaccessible to this pipeline')
    for part in [*reversed(p.parents), p]:
        if part.is_symlink(): raise ValueError('Symlink inputs are not accepted')
    return p


def read_json(path):
    path = safe_path(path)
    return json.loads(path.read_text()) if path.is_file() else None


def write_json(path, payload):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with safe_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def date(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('Acquisition dates require a timezone')
    return result


def source_items(config):
    keys=('image_collection','lidar') if config.get('image_collection') else ('video','telemetry','lidar')
    return [(key,config[key]) for key in keys]


def source_paths(key,source):
    if key=='image_collection':
        metadata=read_json(source['path'])
        return [source['path'],*[record['path'] for record in metadata['images']]]
    if key=='lidar':return [source['metadata_path']] if source.get('ept_url') else source.get('paths',[])
    return [source['path']]


def check_manifest(manifest_path=MANIFEST):
    config=read_json(manifest_path)
    if config is None:return None,['Confirmed OpenDroneMap image collection and matching USGS LiDAR manifest is missing']
    missing=[]
    if config.get('confirmed_sources') is not True:missing.append('Public image license, EXIF GPS and matching USGS coverage must be confirmed')
    if config.get('image_collection'):
        collection=config['image_collection']
        if not collection.get('license') or not collection.get('license_url'):missing.append('Image collection needs an explicit source license')
        if not collection.get('capture_date'):missing.append('Actual EXIF capture date is required')
        try:
            metadata=read_json(collection['path'])
            if not metadata or len(metadata.get('images',[]))<8:missing.append('Downloaded original image collection is incomplete')
        except (KeyError,ValueError,OSError):missing.append('Image collection metadata is unavailable')
    else:
        for key in ('video','telemetry'):
            try:date(config.get(key,{}).get('acquired_at'))
            except (ValueError,TypeError):missing.append(f'{key}: actual acquisition time is required')
        if config.get('telemetry',{}).get('time_reference')!='video_seconds':missing.append('Telemetry must be synchronized to video seconds')
    try:
        for key,source in source_items(config):
            if not source.get('source_url'):missing.append(f'{key}: source URL is required')
            paths=source_paths(key,source)
            if not paths:missing.append(f'{key}: exact source files are missing')
            for value in paths:
                if not safe_path(value).is_file():missing.append(f'{key}: an exact source file is unavailable')
    except (KeyError,ValueError,OSError):missing.append('Source manifest paths are incomplete or inaccessible')
    lidar=config.get('lidar',{})
    if lidar.get('ept_url') and not re.fullmatch(r'https://s3-us-west-2\.amazonaws\.com/usgs-lidar-public/[A-Za-z0-9_-]+/ept\.json',lidar['ept_url']):missing.append('LiDAR endpoint must be the confirmed public USGS EPT project')
    if lidar.get('vertical_unit')!='metre':missing.append('LiDAR Z units must be explicitly confirmed as metres')
    if not lidar.get('vertical_datum'):missing.append('LiDAR vertical datum or documented uncertainty must be explicit')
    try:
        crs=CRS.from_user_input(config.get('output_crs'))
        if not crs.is_projected or any(axis.unit_name!='metre' for axis in crs.axis_info[:2]):raise ValueError()
    except Exception:missing.append('A projected metric CRS is required')
    bounds=config.get('bounds')
    if not isinstance(bounds,list) or len(bounds)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in bounds) or bounds[0]>=bounds[2] or bounds[1]>=bounds[3]:missing.append('AOI bounds [west,south,east,north] in output_crs are required')
    return config,list(dict.fromkeys(missing))


def source_file_stats(config):
    result={}
    for key,source in source_items(config):
        result[key]=[]
        for value in source_paths(key,source):
            stat=safe_path(value).stat();result[key].append({'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})
    return result


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def get_status(manifest_path=MANIFEST):
    from sim.deployment import frozen_snapshot
    snapshot=frozen_snapshot()
    if snapshot:return snapshot['drone_status']
    config, missing = check_manifest(manifest_path)
    latest = read_json(BASE / 'status.json')
    result = {'label': LABEL, 'available': False, 'metrics': None, 'missing': missing,
              'tools': {name: executable(name) is not None for name in TOOLS},
              'assets': {}, 'steps': {name: {'available': False, 'reason': 'No fresh, confirmed run'} for name in STAGES}}
    matching=bool(latest and config and not missing and latest.get('manifest_sha256') == config_digest(config))
    if matching and latest.get('source_file_stats') != source_file_stats(config):
        matching=False
        missing.append('Source files changed after this run; start a fresh run')
    if matching:
        result.update(latest)
        result['missing'] = missing + latest.get('missing', [])
        result['available'] = bool(latest.get('available')) and not result['missing']
        if not result['available']: result['metrics'] = None
    elif latest:
        result['missing'].append('Previous run does not match the current confirmed manifest; start a fresh run')
    result['source_manifest_available'] = config is not None
    return result



def current_metrics():
    status=get_status()
    if not status.get('available') or status.get('metrics') != str(METRICS.relative_to(ROOT)):
        raise ValueError('No validated metrics for the current run')
    metrics=read_json(METRICS)
    if not metrics or metrics.get('run_id')!=status.get('run_id'):
        raise ValueError('Metrics do not match the current run')
    return metrics


def asset_path(relative):
    # Validate lexical shape before any filesystem operation or metadata access.
    pattern=r'data/derived/drone_proof/run_\d{8}T\d{12}Z/(?:source_preview\.mp4|frames/frame_\d{6}\.jpg|sparse\.ply|dense/scene_dense(?:_preview)?\.ply|(?:fresh|lidar)_ndsm_0p5m\.(?:tif|json|bin)|validation\.json)'
    if not isinstance(relative,str) or not re.fullmatch(pattern,relative):
        raise ValueError('Asset is not a public proof product')
    status=get_status();allowed=set()
    for value in status.get('assets',{}).values():
        if isinstance(value,str):allowed.add(value)
        elif isinstance(value,list):allowed.update(item for item in value if isinstance(item,str))
    for value in tuple(allowed):
        if re.search(r'(?:fresh|lidar)_ndsm_0p5m\.json$',value):allowed.add(value[:-5]+'.bin')
    if relative not in allowed:raise ValueError('Asset was not generated by a completed stage of this run')
    path=safe_path(relative)
    if not path.is_file():raise ValueError('Computed asset is not available')
    return path


def need_sources(manifest_path):
    config, missing = check_manifest(manifest_path)
    if missing: raise ValueError('; '.join(missing))
    return config


def executable(name):
    found=shutil.which(name)
    if found:return found
    if name in ('InterfaceCOLMAP','DensifyPointCloud'):
        metadata=read_json(ROOT/'data'/'raw'/'drone_proof'/'tools'/'openmvs-2.4.0'/'tooling.json')
        path=(metadata or {}).get('programs',{}).get(name)
        if path and safe_path(path).is_file():return path
    return None


def need_tools(*names):
    missing = [name for name in names if executable(name) is None]
    if missing: raise RuntimeError('Required local tools are not installed: ' + ', '.join(missing))


THREAD_LIMIT = 2
NICE_PRIORITY = 15


def bounded_environment():
    settings=os.environ.copy()
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        settings[name]=str(THREAD_LIMIT)
    settings['OMP_THREAD_LIMIT']=str(THREAD_LIMIT)
    return settings


def command(run, args):
    """Bounded native processes; arguments only and no configuration-file loading."""
    args=[executable(str(args[0])) or args[0],*args[1:]]
    adjustment=max(0,NICE_PRIORITY-os.getpriority(os.PRIO_PROCESS,0))
    args=['/usr/bin/nice','-n',str(adjustment),*args]
    with (run / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps({'started_at':datetime.now(timezone.utc).isoformat(),'argv':list(map(str,args)),'resource_policy':{'max_threads':THREAD_LIMIT,'nice':NICE_PRIORITY,'gpu':False}})+'\n')
    with (run / 'process.log').open('a') as log:
        subprocess.run(list(map(str,args)),check=True,cwd=run,stdout=log,stderr=subprocess.STDOUT,env=bounded_environment())


def colmap_help(stage):
    # These exact installed 4.2.1 options were checked once before the limited run.
    path=ROOT/'data'/'raw'/'drone_proof'/'tools'/'colmap-4.2.1'/(stage+'_help.txt')
    if not path.is_file():raise ValueError('COLMAP option help must be checked once before bounded execution')
    return path.read_text()


def archive_interrupted_sparse(run):
    """Preserve unfinished products; the resumed stage starts with an empty database."""
    proof=read_json(run/'proof.json')
    if any(proof['steps'][stage].get('available') for stage in ('features','sparse','gps_alignment','dense','ndsm','lidar','validation')):
        raise ValueError('Resource restart applies only to unfinished feature extraction')
    paths=[run/name for name in ('database.db','database.db-wal','database.db-shm','database.db-journal','sparse','sparse_text','sparse.ply')]
    existing=[safe_path(path) for path in paths if path.exists()]
    if not existing:raise ValueError('No interrupted sparse-stage artifacts to preserve')
    stamp=datetime.now(timezone.utc);archive=run/('interrupted_sparse_'+stamp.strftime('%Y%m%dT%H%M%S%fZ'));archive.mkdir()
    for path in existing:path.rename(archive/path.name)
    record={'date':stamp.isoformat(),'reason':'User requested browser responsiveness; replace stopped unbounded extraction with at most two threads and nice15','archive':str(archive.relative_to(ROOT)),'preserved_products':[path.name for path in existing],'reused_partial_database':False}
    write_json(archive/'interruption.json',record)
    proof.setdefault('resource_resumptions',[]).append(record)
    proof['resource_policy']={'max_threads':THREAD_LIMIT,'nice':NICE_PRIORITY,'gpu':False}
    write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    return record


def archive_interrupted_dense(run):
    require_stage(run,'gps_alignment')
    proof=read_json(run/'proof.json')
    if proof['steps']['dense'].get('available'):raise ValueError('Completed dense output must never be replaced by a partial retry')
    source=safe_path(run/'dense')
    if not source.is_dir():raise ValueError('No failed dense preparation to preserve')
    stamp=datetime.now(timezone.utc);archive=run/('interrupted_dense_'+stamp.strftime('%Y%m%dT%H%M%S%fZ'))
    source.rename(archive)
    record={'date':stamp.isoformat(),'reason':'OpenMVS relative image paths require the dense working folder; preserve failed preparation and regenerate from this run GPS model','archive':str(archive.relative_to(ROOT)),'reused_dense_output':False}
    write_json(archive/'interruption.json',record)
    proof.setdefault('resource_resumptions',[]).append(record)
    write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    return record


def require_stage(run, stage):
    proof = read_json(run/'proof.json')
    if not proof or not proof.get('steps', {}).get(stage, {}).get('available'):
        raise ValueError(f'This run has no completed {stage} stage')


def update(run, stage, details):
    proof = read_json(run / 'proof.json')
    proof['steps'][stage] = {'available': True, **details}
    proof['updated_at'] = datetime.now(timezone.utc).isoformat()
    write_json(run / 'proof.json', proof)
    write_json(BASE / 'status.json', proof)
    return proof


def telemetry(config):
    with safe_path(config['telemetry']['path']).open() as stream:
        rows = list(csv.DictReader(stream))
    required = ('time_s','latitude','longitude','altitude_m')
    values = np.array([[float(row[k]) for k in required] for row in rows])
    if len(values)<3 or not np.isfinite(values).all() or np.any(np.diff(values[:,0])<=0):
        raise ValueError('Telemetry CSV needs strictly increasing time_s, latitude, longitude, altitude_m')
    if np.any(np.abs(values[:,1])>90) or np.any(np.abs(values[:,2])>180): raise ValueError('Invalid telemetry coordinates')
    return values


def select_frames(config, manifest_path=MANIFEST):
    need_tools('ffmpeg','ffprobe')
    gps = telemetry(config)
    stamp = datetime.now(timezone.utc)
    run = BASE / ('run_' + stamp.strftime('%Y%m%dT%H%M%S%fZ'))
    run.mkdir(parents=True, exist_ok=False)
    (run/'frames').mkdir(); (run/'candidates').mkdir()
    sources = {}
    for key in ('video','telemetry','lidar'):
        entry = config[key]
        paths = entry['paths'] if key=='lidar' else [entry['path']]
        sources[key] = {**entry, 'sha256': [sha256(path) for path in paths]}
    write_json(run/'inputs.json', config)
    areas = config.get('validation', {}).get('areas_geojson')
    declared_areas_hash = sha256(areas) if areas and safe_path(areas).is_file() else None
    proof = {'label': LABEL, 'run_id': run.name, 'run_directory': str(run.relative_to(ROOT)),
             'run_date': stamp.isoformat(), 'manifest_sha256': config_digest(config), 'validation_areas_sha256': declared_areas_hash, 'source_file_stats': source_file_stats(config), 'available': False, 'metrics': None, 'sources': sources,
             'steps': {name:{'available':False,'reason':'Not computed in this run'} for name in STAGES},
             'assets': {}, 'missing': []}
    write_json(run/'proof.json',proof)
    write_json(BASE/'status.json',proof)
    video = safe_path(config['video']['path'])
    meta = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)]))
    stream = next(item for item in meta['streams'] if item['codec_type']=='video')
    duration = float(stream.get('duration',meta['format'].get('duration')))
    interval = float(config.get('frame_interval_s',2)); candidate_fps = float(config.get('candidate_fps',2))
    if not 0.2<=interval<=20 or not 0.5<=candidate_fps<=10: raise ValueError('Invalid frame sampling interval')
    command(run,['ffmpeg','-nostdin','-v','error','-i',video,'-vf',f'fps={candidate_fps}','-q:v','2',run/'candidates'/'frame_%06d.jpg'])
    selected, candidates = {}, []
    for index in range(1,math.ceil(duration*candidate_fps)+2):
        source=run/'candidates'/f'frame_{index:06d}.jpg'
        if not source.is_file(): break
        time_s=(index-1)/candidate_fps
        if time_s<gps[0,0] or time_s>gps[-1,0]: continue
        raw=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(source),'-vf','scale=320:180','-pix_fmt','gray','-f','rawvideo','pipe:1'])
        gray=np.frombuffer(raw,dtype='uint8').reshape(180,320).astype(float)
        sharpness=float(np.var(ndimage.laplace(gray)))
        bucket=int(time_s/interval)
        item={'source':str(source),'video_time_s':time_s,'sharpness_laplacian_variance':sharpness}
        candidates.append(item)
        if bucket not in selected or sharpness>selected[bucket]['sharpness_laplacian_variance']: selected[bucket]=item
    frames=[]
    for index,item in enumerate(selected.values(),1):
        filename=f'frame_{index:06d}.jpg'
        shutil.copyfile(item['source'],run/'frames'/filename)
        coordinates=[float(np.interp(item['video_time_s'],gps[:,0],gps[:,j])) for j in (1,2,3)]
        frames.append({**{k:v for k,v in item.items() if k!='source'},'name':filename,'latitude':coordinates[0],'longitude':coordinates[1],'altitude_m':coordinates[2]})
    if len(frames)<8: raise ValueError('Fewer than eight real frames have matching telemetry; no reconstruction started')
    write_json(run/'frames.json',{'frames':frames,'candidate_count':len(candidates),'selection':'Sharpest sampled frame per time interval; nominal fps-filter timestamps; telemetry interpolated without extrapolation','video_metadata':meta})
    command(run,['ffmpeg','-nostdin','-v','error','-i',video,'-t',str(min(30,duration)),'-an','-c:v','libx264','-crf','23','-pix_fmt','yuv420p',run/'source_preview.mp4'])
    proof=update(run,'frames',{'count':len(frames),'path':str((run/'frames.json').relative_to(ROOT))})
    proof['assets']['video']=str((run/'source_preview.mp4').relative_to(ROOT))
    proof['assets']['frames']=[str((run/'frames'/f['name']).relative_to(ROOT)) for f in frames]
    write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    return run


def select_images(config):
    """Fresh selection from hash-verified original photographs, retaining actual EXIF."""
    need_tools('ffmpeg')
    collection=read_json(config['image_collection']['path']);records=collection['images']
    stamp=datetime.now(timezone.utc);run=BASE/('run_'+stamp.strftime('%Y%m%dT%H%M%S%fZ'));run.mkdir(parents=True,exist_ok=False);(run/'frames').mkdir()
    sources={key:{**source,'sha256':[sha256(path) for path in source_paths(key,source)]} for key,source in source_items(config)}
    areas=config.get('validation',{}).get('areas_geojson')
    proof={'label':LABEL,'run_id':run.name,'run_directory':str(run.relative_to(ROOT)),'run_date':stamp.isoformat(),'manifest_sha256':config_digest(config),'source_file_stats':source_file_stats(config),'validation_areas_sha256':sha256(areas) if areas else None,'sources':sources,'available':False,'metrics':None,'missing':[],'assets':{},'steps':{name:{'available':False,'reason':'Not computed in this fresh run'} for name in STAGES}}
    write_json(run/'inputs.json',config);write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    frames=[]
    for record in records:
        path=safe_path(record['path'])
        if sha256(path)!=record['sha256']:raise ValueError('Original image bytes differ from acquisition manifest')
        if not all(math.isfinite(float(record[k])) for k in ('latitude','longitude','altitude_m')):continue
        raw=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-threads','2','-filter_threads','2','-i',str(path),'-vf','scale=320:240','-pix_fmt','gray','-f','rawvideo','pipe:1'])
        values=np.frombuffer(raw,dtype='uint8').reshape(240,320).astype(float);sharpness=float(np.var(ndimage.laplace(values)))
        name=f'frame_{len(frames)+1:06d}.jpg';shutil.copyfile(path,run/'frames'/name)
        frames.append({**record,'original_name':record['name'],'name':name,'sharpness_laplacian_variance':sharpness})
    if len(frames)<8:raise ValueError('Insufficient real photos with valid GPS')
    write_json(run/'frames.json',{'frames':frames,'candidate_count':len(records),'selection':'All hash-verified original photos with finite EXIF GPS and successful decode; sharpness measured, no synthetic images','source_kind':'image_collection'})
    proof=update(run,'frames',{'count':len(frames),'path':str((run/'frames.json').relative_to(ROOT)),'source_kind':'image_collection'})
    proof['assets']['frames']=[str((run/'frames'/frame['name']).relative_to(ROOT)) for frame in frames]
    write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof);return run


def load_run(value):
    run=safe_path(value)
    if run.parent != BASE or not re.fullmatch(r'run_\d{8}T\d{12}Z',run.name):
        raise ValueError('Run must be a fresh directory created by this pipeline')
    proof=read_json(run/'proof.json')
    if not proof or not proof.get('run_date'): raise ValueError('No fresh run provenance')
    config = read_json(run/'inputs.json')
    if proof.get('manifest_sha256') != config_digest(config): raise ValueError('Run inputs changed')
    for key,source in source_items(config):
        paths = source_paths(key,source)
        if [sha256(path) for path in paths] != proof['sources'][key]['sha256']:
            raise ValueError('Source bytes changed; start a fresh run')
    return run, config


def sparse(run,config):
    require_stage(run, 'frames')
    need_tools('colmap')
    if (run/'database.db').exists() or (run/'sparse').exists(): raise ValueError('Sparse stage already exists; create a new run instead of reusing outputs')
    (run/'sparse').mkdir();(run/'sparse_text').mkdir()
    extraction_help=colmap_help('feature_extractor')
    gpu='FeatureExtraction.use_gpu' if 'FeatureExtraction.use_gpu' in extraction_help else 'SiftExtraction.use_gpu'
    size_option='FeatureExtraction.max_image_size' if 'FeatureExtraction.max_image_size' in extraction_help else 'SiftExtraction.max_image_size'
    if 'FeatureExtraction.num_threads' not in extraction_help:raise ValueError('Installed COLMAP lacks the verified extraction thread option')
    command(run,['colmap','feature_extractor','--FeatureExtraction.num_threads',str(THREAD_LIMIT),'--database_path',run/'database.db','--image_path',run/'frames','--ImageReader.single_camera','1','--ImageReader.camera_model','SIMPLE_RADIAL','--'+gpu,'0','--'+size_option,str(config.get('max_image_size',1800))])
    with sqlite3.connect(run/'database.db') as database:
        count=database.execute('SELECT SUM(rows) FROM keypoints').fetchone()[0]
    update(run,'features',{'count':int(count or 0),'measurement':'Sum of COLMAP keypoint rows in this fresh run'})
    matcher='exhaustive_matcher' if config.get('image_collection') else 'sequential_matcher'
    match_help=colmap_help(matcher)
    gpu='FeatureMatching.use_gpu' if 'FeatureMatching.use_gpu' in match_help else 'SiftMatching.use_gpu'
    if 'FeatureMatching.num_threads' not in match_help:raise ValueError('Installed COLMAP lacks the verified matching thread option')
    command(run,['colmap',matcher,'--FeatureMatching.num_threads',str(THREAD_LIMIT),'--database_path',run/'database.db','--'+gpu,'0'])
    if 'Mapper.num_threads' not in colmap_help('mapper'):raise ValueError('Installed COLMAP lacks the verified mapping thread option')
    command(run,['colmap','mapper','--Mapper.num_threads',str(THREAD_LIMIT),'--database_path',run/'database.db','--image_path',run/'frames','--output_path',run/'sparse','--Mapper.multiple_models','0'])
    if not (run/'sparse'/'0').is_dir(): raise ValueError('COLMAP did not produce the expected connected model')
    command(run,['colmap','model_converter','--input_path',run/'sparse'/'0','--output_path',run/'sparse_text','--output_type','TXT'])
    command(run,['colmap','model_converter','--input_path',run/'sparse'/'0','--output_path',run/'sparse.ply','--output_type','PLY'])
    count=sum(1 for line in (run/'sparse_text'/'points3D.txt').open() if line.strip() and not line.startswith('#'))
    proof=update(run,'sparse',{'point_count':count,'intrinsics':'Estimated by COLMAP SIMPLE_RADIAL; inspect reprojection error before claiming accuracy'})
    proof['assets']['sparse']=str((run/'sparse.ply').relative_to(ROOT));write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)


def similarity(source,target):
    source=np.asarray(source,float);target=np.asarray(target,float)
    if source.shape!=target.shape or source.ndim!=2 or len(source)<3 or not np.isfinite(source).all() or not np.isfinite(target).all(): raise ValueError('At least three paired points are required')
    a=source-source.mean(axis=0);b=target-target.mean(axis=0)
    if np.linalg.matrix_rank(a)<2: raise ValueError('Collinear controls cannot constrain a 3D alignment')
    u,s,v=np.linalg.svd(b.T@a/len(a));sign=np.eye(source.shape[1]);sign[-1,-1]=np.sign(np.linalg.det(u@v))
    rotation=u@sign@v;scale=float(np.sum(s*np.diag(sign))/np.mean(np.sum(a*a,axis=1)))
    offset=target.mean(axis=0)-scale*rotation@source.mean(axis=0)
    return scale,rotation,offset


def transformed_pose(rotation_camera, translation_camera, scale, rotation_world, translation_world):
    rotation=rotation_camera@rotation_world.T
    translation=scale*translation_camera-rotation@translation_world
    return rotation,translation


def transform_colmap_text(source,destination,scale,rotation,offset):
    """Transform documented COLMAP text poses directly, independent of CLI version."""
    shutil.copyfile(source/'cameras.txt',destination/'cameras.txt')
    lines=(source/'images.txt').read_text().splitlines();result=[];index=0
    while index<len(lines):
        line=lines[index];index+=1
        if not line.strip() or line.startswith('#'):result.append(line);continue
        fields=line.split();q=np.array(list(map(float,fields[1:5])))
        camera=Rotation.from_quat([q[1],q[2],q[3],q[0]]).as_matrix()
        camera,t=transformed_pose(camera,np.array(list(map(float,fields[5:8]))),scale,rotation,offset)
        q=Rotation.from_matrix(camera).as_quat()
        result.append(' '.join([fields[0],*[format(v,'.17g') for v in (q[3],q[0],q[1],q[2],*t)],*fields[8:]]))
        if index<len(lines):result.append(lines[index]);index+=1
    (destination/'images.txt').write_text('\n'.join(result)+'\n')
    with (source/'points3D.txt').open() as original,(destination/'points3D.txt').open('x') as target:
        for line in original:
            if not line.strip() or line.startswith('#'):target.write(line);continue
            fields=line.split();xyz=scale*rotation@np.array(list(map(float,fields[1:4])))+offset
            target.write(' '.join([fields[0],*[format(v,'.17g') for v in xyz],*fields[4:]])+'\n')


def align_gps(run,config):
    require_stage(run, 'sparse')
    need_tools('colmap')
    if (run/'aligned').exists(): raise ValueError('Alignment already exists; create a fresh run')
    frame_by_name={f['name']:f for f in read_json(run/'frames.json')['frames']}
    lines=(run/'sparse_text'/'images.txt').read_text().splitlines()
    centers=[];references=[];names=[];project=Transformer.from_crs(4326,config['output_crs'],always_xy=True)
    line_index=0
    while line_index<len(lines):
        line=lines[line_index];line_index+=1
        if not line.strip() or line.startswith('#'):continue
        fields=line.split();line_index+=1
        if len(fields)<10 or fields[9] not in frame_by_name:continue
        q=np.array(list(map(float,fields[1:5])));t=np.array(list(map(float,fields[5:8])))
        rotation=Rotation.from_quat([q[1],q[2],q[3],q[0]]).as_matrix()
        frame=frame_by_name[fields[9]];x,y=project.transform(frame['longitude'],frame['latitude'])
        centers.append(-rotation.T@t);references.append([x-config['bounds'][0],y-config['bounds'][1],frame['altitude_m']]);names.append(fields[9])
    scale,rotation,offset=similarity(centers,references)
    matrix=np.eye(4);matrix[:3,:3]=scale*rotation;matrix[:3,3]=offset
    np.savetxt(run/'gps_transform.txt',matrix)
    (run/'aligned').mkdir()
    transform_colmap_text(run/'sparse_text',run/'aligned',scale,rotation,offset)
    residual=np.asarray(centers)@(scale*rotation).T+offset-np.asarray(references)
    record={'scale':scale,'rotation_matrix':rotation.tolist(),'translation_m':offset.tolist(),'gps_camera_rmse_m':float(np.sqrt(np.mean(np.sum(residual*residual,axis=1)))),'camera_count':len(names),'horizontal_crs':config['output_crs'],'local_origin_m':[config['bounds'][0],config['bounds'][1],0],'vertical_datum':config.get('telemetry',config.get('image_collection',{})).get('vertical_datum','EXIF altitude: exact vertical datum unspecified'),'note':'GPS fit residual is not independent LiDAR validation. Altitudes retain the explicitly supplied telemetry datum.'}
    write_json(run/'sparse_alignment.json',record);update(run,'gps_alignment',record)


def dense(run,config):
    require_stage(run, 'gps_alignment')
    need_tools('colmap','InterfaceCOLMAP','DensifyPointCloud')
    if (run/'dense').exists():raise ValueError('Dense stage already exists; previous outputs cannot be reused')
    (run/'dense').mkdir()
    if '--num_threads' not in colmap_help('image_undistorter'):raise ValueError('Installed COLMAP lacks the verified undistortion thread option')
    command(run,['colmap','image_undistorter','--num_threads',str(THREAD_LIMIT),'--image_path',run/'frames','--input_path',run/'aligned','--output_path',run/'dense','--output_type','COLMAP'])
    command(run,['InterfaceCOLMAP','-w',run/'dense','--max-threads',str(THREAD_LIMIT),'-i',run/'dense','-o',run/'dense'/'scene.mvs','--image-folder',run/'dense'/'images'])
    help_text=(ROOT/'data'/'raw'/'drone_proof'/'tools'/'openmvs-2.4.0'/'DensifyPointCloud_help.txt').read_text()
    # GPU options are absent in CPU-only builds; names vary by OpenMVS version.
    cpu=['--gpu-device','-2'] if 'gpu-device' in help_text else ['--cuda-device','-2'] if 'cuda-device' in help_text else []
    command(run,['DensifyPointCloud','-w',run/'dense','-i',run/'dense'/'scene.mvs','-o',run/'dense'/'scene_dense.mvs','--max-resolution',str(config.get('max_image_size',1800)),'--max-threads',str(THREAD_LIMIT),*cpu])
    cloud=run/'dense'/'scene_dense.ply'
    if not cloud.is_file():raise ValueError('OpenMVS did not produce a fresh dense cloud')
    proof=update(run,'dense',{'backend':'OpenMVS CPU','generated_at':datetime.now(timezone.utc).isoformat()})
    proof['assets']['dense']=str(cloud.relative_to(ROOT));write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)


def pdal_grid(run,reader,filters,path,bounds,statistic):
    west,south,east,north=bounds
    writer={'type':'writers.gdal','filename':str(path),'resolution':.5,'bounds':f'([{west},{east}],[{south},{north}])','output_type':statistic,'nodata':-9999,'data_type':'float','gdalopts':'COMPRESS=DEFLATE,TILED=YES','radius':1.0,'window_size':0}
    readers=reader if isinstance(reader,list) else [reader]
    pipeline=run/(path.stem+'_pipeline.json');write_json(pipeline,{'pipeline':[*readers,*filters,writer]})
    command(run,['pdal','pipeline',pipeline])


def pull_lidar(run,config):
    source=config['lidar'];destination=safe_path(source['local_path'])
    metadata_path=destination.with_suffix('.json')
    if destination.is_file() and metadata_path.is_file():
        recorded=read_json(metadata_path)
        if recorded.get('sha256')!=sha256(destination):raise ValueError('Cached reference crop changed')
        return destination
    ept=read_json(source['metadata_path']);project=Transformer.from_crs(config['output_crs'],ept['srs']['wkt'],always_xy=True)
    w,s,e,n=project.transform_bounds(*config['bounds'],densify_pts=21)
    temporary=destination.with_suffix('.part.laz');destination.parent.mkdir(parents=True,exist_ok=True)
    west,south,east,north=config['bounds']
    pipeline={'pipeline':[{'type':'readers.ept','filename':source['ept_url'],'bounds':f'([{w},{e}],[{s},{n}])','requests':THREAD_LIMIT},
                          {'type':'filters.reprojection','out_srs':config['output_crs']},
                          {'type':'filters.crop','bounds':f'([{west},{east}],[{south},{north}])'},
                          {'type':'writers.las','filename':str(temporary),'a_srs':config['output_crs'],'minor_version':4,'dataformat_id':6,'compression':True,'scale_x':.001,'scale_y':.001,'scale_z':.001,'offset_x':'auto','offset_y':'auto','offset_z':'auto'}]}
    path=run/'usgs_ept_crop_pipeline.json';write_json(path,pipeline);command(run,['pdal','pipeline',path]);temporary.replace(destination)
    write_json(metadata_path,{'source_url':source['ept_url'],'bounds':config['bounds'],'crs':config['output_crs'],'sha256':sha256(destination),'downloaded_at':datetime.now(timezone.utc).isoformat(),'vertical_datum':source['vertical_datum'],'metadata_source_url':source['datum_source_url'],'resolution':'Full density EPT; no resolution thinning requested'})
    return destination


def interpolate_ground(path,max_distance_m=20):
    """Interpolate each source's own observed ground within its hull and distance cap."""
    from scipy.interpolate import LinearNDInterpolator
    with rasterio.open(path) as raster:
        values=raster.read(1,masked=True).filled(np.nan);profile=raster.profile;cell=abs(raster.transform.a)
    valid=np.isfinite(values)
    if np.count_nonzero(valid)<10:raise ValueError('Too few classified ground observations')
    distance=ndimage.distance_transform_edt(~valid)*cell;target=(~valid)&(distance<=max_distance_m)
    rows,columns=np.nonzero(valid);target_rows,target_columns=np.nonzero(target)
    interpolation=LinearNDInterpolator(np.column_stack((rows,columns)),values[valid],fill_value=np.nan)
    if len(target_rows):values[target_rows,target_columns]=interpolation(target_rows,target_columns)
    filled=np.isfinite(values)&~valid
    profile.update(nodata=-9999)
    with rasterio.open(path,'w',**profile) as output:output.write(np.where(np.isfinite(values),values,-9999).astype('float32'),1)
    return {'observed_cells':int(valid.sum()),'interpolated_cells':int(filled.sum()),'max_distance_m':max_distance_m,'method':'Linear triangles from this source only, inside ground convex hull; cells farther than limit stay missing'}


def rasterize(run,config):
    require_stage(run, 'dense')
    need_tools('pdal')
    if (run/'fresh_dsm_0p5m.tif').exists():raise ValueError('Raster products already exist; create a fresh run')
    bounds=config['bounds'];crs=config['output_crs']
    from .ply_scalar import scalar_ply
    adapted=run/'dense'/'scene_dense_scalar.ply'
    scalar_ply(run/'dense'/'scene_dense.ply',adapted,progress=lambda done,total:print(f'PLY compatibility adapter: {done}/{total} preserved vertices',flush=True))
    export_dense_preview(run)
    reader={'type':'readers.ply','filename':str(adapted),'override_srs':crs}
    shift={'type':'filters.transformation','matrix':f'1 0 0 {bounds[0]} 0 1 0 {bounds[1]} 0 0 1 0 0 0 0 1'}
    pdal_grid(run,reader,[shift],run/'fresh_dsm_0p5m.tif',bounds,'max')
    pdal_grid(run,reader,[shift,{'type':'filters.smrf'},{'type':'filters.range','limits':'Classification[2:2]'}],run/'fresh_ground_0p5m.tif',bounds,'idw')
    # LiDAR is loaded only here, after reconstruction and GPS alignment.
    lidar_paths=[str(pull_lidar(run,config))] if config['lidar'].get('ept_url') else [str(safe_path(path)) for path in config['lidar']['paths']]
    lidar=[{'type':'readers.las','filename':path} for path in lidar_paths]
    reprojection=[{'type':'filters.merge'},{'type':'filters.reprojection','out_srs':crs},{'type':'filters.expression','expression':'Classification != 7 && Classification != 18'}]
    pdal_grid(run,lidar,reprojection,run/'lidar_dsm_0p5m.tif',bounds,'max')
    pdal_grid(run,lidar,[*reprojection,{'type':'filters.range','limits':'Classification[2:2]'}],run/'lidar_ground_0p5m.tif',bounds,'idw')
    ground_methods={}
    for prefix in ('fresh','lidar'):
        ground_methods[prefix]=interpolate_ground(run/f'{prefix}_ground_0p5m.tif')
        with rasterio.open(run/f'{prefix}_dsm_0p5m.tif') as dsm, rasterio.open(run/f'{prefix}_ground_0p5m.tif') as ground:
            if dsm.shape!=ground.shape or dsm.transform!=ground.transform:raise ValueError('DSM and ground grids are not aligned')
            a=dsm.read(1,masked=True);b=ground.read(1,masked=True);result=(a-b).filled(-9999).astype('float32')
            if not np.any(result!=-9999):raise ValueError(f'{prefix} nDSM has no observed DSM/ground overlap; do not fill it from the other source')
            profile=dsm.profile
        with rasterio.open(run/f'{prefix}_ndsm_0p5m.tif','w',**profile) as dst:dst.write(result,1)
    proof=update(run,'ndsm',{'resolution_m':.5,'ground_method':ground_methods['fresh'],'generated_at':datetime.now(timezone.utc).isoformat()})
    proof=update(run,'lidar',{'project':config['lidar']['project'],'vertical_datum':config['lidar']['vertical_datum'],'acquired_at':config['lidar'].get('acquired_at'),'acquisition_range':config['lidar'].get('acquisition_range'),'ground_method':ground_methods['lidar']})
    for key in ('fresh_ndsm','lidar_ndsm'):
        proof['assets'][key]=str((run/f'{key}_0p5m.tif').relative_to(ROOT))
        grid = export_grid(run/f'{key}_0p5m.tif')
        proof['assets'][key+'_grid']=str(grid.relative_to(ROOT))
    write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)


def export_dense_preview(run,config=None):
    """Publish a bounded actual-point view without changing full clouds or metrics."""
    require_stage(run, 'dense')
    from .ply_scalar import scalar_ply, sampled_preview
    full=run/'dense'/'scene_dense.ply';scalar=run/'dense'/'scene_dense_scalar.ply'
    scalar_ply(full,scalar)
    preview=run/'dense'/'scene_dense_preview.ply'
    metadata=sampled_preview(scalar,preview)
    proof=read_json(run/'proof.json')
    proof['assets']['dense_original']=str(full.relative_to(ROOT))
    proof['assets']['dense']=str(preview.relative_to(ROOT))
    proof['steps']['dense'].update(point_count=metadata['source_point_count'],preview_point_count=metadata['preview_point_count'])
    proof['dense_preview']={'source_point_count':metadata['source_point_count'],'preview_point_count':metadata['preview_point_count'],'sampling_stride':metadata['sampling_stride'],'note':'Display-only deterministic sample of actual reconstructed points. Full-resolution cloud and validation metrics are unchanged.'}
    proof['updated_at']=datetime.now(timezone.utc).isoformat()
    write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    return metadata


def export_grid(path):
    """Actual raster bytes for browser rendering; NaN is explicit unobserved data."""
    with rasterio.open(path) as source:
        values=source.read(1,masked=True).filled(np.nan).astype('<f4')
        finite=values[np.isfinite(values)]
        binary=path.with_suffix('.bin');values.tofile(binary)
        metadata={'width':source.width,'height':source.height,'cell_size_m':.5,'crs':source.crs.to_string(),
                  'transform':list(source.transform)[:6],'min':float(finite.min()) if len(finite) else None,
                  'max':float(finite.max()) if len(finite) else None,'observed_cells':int(len(finite)),
                  'dtype':'Float32 little-endian','layout':'row-major, north to south','nodata':'NaN',
                  'binary':str(binary.relative_to(ROOT))}
    output=path.with_suffix('.json');write_json(output,metadata);return output


def nmad(values):
    values=np.asarray(values,float);values=values[np.isfinite(values)]
    return float(1.4826*np.median(np.abs(values-np.median(values)))) if len(values) else None


def checkpoint_validation(rows):
    """Fit only translation on fit rows. Never correct scale/rotation/tilt away."""
    identifiers=[row['id'] for row in rows]
    if len(set(identifiers))!=len(identifiers):raise ValueError('Checkpoint IDs must be unique across fit and evaluation')
    if any(r['split'] not in ('fit','held_out') for r in rows):raise ValueError('Unknown checkpoint split')
    fit=[r for r in rows if r['split']=='fit'];held=[r for r in rows if r['split']=='held_out']
    if len(fit)<3 or len(held)<4:raise ValueError('Need at least three fit and four held-out checkpoints')
    keys=('ours_x','ours_y','ours_z','lidar_x','lidar_y','lidar_z')
    a=np.array([[float(r[k]) for k in keys] for r in fit]);b=np.array([[float(r[k]) for k in keys] for r in held])
    if not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Checkpoint coordinates must be finite')
    if any(np.any(np.linalg.norm(a[:,None,start:start+2]-b[None,:,start:start+2],axis=2)<1) for start in (0,3)):raise ValueError('Fit and held-out checkpoints must be spatially separate by at least one metre in both sources')
    if any(str(r.get('stable_ground','')).lower()!='true' for r in fit):raise ValueError('Fit checkpoints must be declared stable ground')
    shift=np.median(a[:,3:]-a[:,:3],axis=0)
    aligned=b[:,:3]+shift;residual=aligned-b[:,3:]
    xy_scale,xy_rotation,_=similarity(aligned[:,:2],b[:,3:5])
    xy=b[:,3:5]-b[:,3:5].mean(axis=0)
    if np.linalg.matrix_rank(xy)<2:raise ValueError('Held-out checkpoints must span a two-dimensional area')
    plane=np.linalg.lstsq(np.column_stack((xy,np.ones(len(xy)))),residual[:,2],rcond=None)[0]
    return {'fit_count':len(fit),'held_out_count':len(held),'fitted_xy_shift_m':shift[:2].tolist(),'fitted_vertical_offset_m':float(shift[2]),'held_out_xy_rmse_m':float(np.sqrt(np.mean(np.sum(residual[:,:2]**2,axis=1)))),'held_out_z_rmse_m':float(np.sqrt(np.mean(residual[:,2]**2))),'held_out_z_nmad_m':nmad(residual[:,2]),'held_out_z_bias_m':float(np.median(residual[:,2])),'scale_error_percent':float((1/xy_scale-1)*100),'rotation_error_deg':float(-math.degrees(math.atan2(xy_rotation[1,0],xy_rotation[0,0]))),'tilt_m_per_km':[float(v*1000) for v in plane[:2]],'tilt_deg':float(math.degrees(math.atan(np.linalg.norm(plane[:2])))),'correction_policy':'Only XYZ translation fitted on fit checkpoints. Scale, rotation and tilt are held-out diagnostics; none is applied as a correction.','held_out_ids':[r['id'] for r in held]}


def validation_regions(areas, geometry_crs, output_crs, rows):
    project=Transformer.from_crs(geometry_crs,output_crs,always_xy=True).transform
    regions={split:[transform(project,shape(f['geometry'])) for f in areas if f['properties']['split']==split] for split in ('fit','held_out')}
    if any(not items for items in regions.values()):raise ValueError('Predeclared fit and held-out polygons are required')
    if any(not g.is_valid or g.is_empty or g.geom_type not in ('Polygon','MultiPolygon') for items in regions.values() for g in items):raise ValueError('Validation regions must be valid, nonempty polygons')
    if any(a.distance(b)<1 for a in regions['fit'] for b in regions['held_out']):raise ValueError('Fit and held-out validation areas need a one-metre separation')
    for row in rows:
        point=Point(float(row['lidar_x']),float(row['lidar_y']))
        if row['split'] not in regions or not any(g.contains(point) for g in regions[row['split']]):
            raise ValueError('Each checkpoint must be inside its predeclared split region')
    return regions


def checkpoint_surface_heights(run, rows):
    # CSV defines matched XY landmarks; Z comes only from this run's actual grids.
    result=[]
    for row in rows:
        kind='ground' if row['split']=='fit' else 'dsm'
        row=dict(row)
        for prefix,field in (('fresh','ours'),('lidar','lidar')):
            with rasterio.open(run/f'{prefix}_{kind}_0p5m.tif') as source:
                value=next(source.sample([(float(row[field+'_x']),float(row[field+'_y']))],masked=True))[0]
                if np.ma.is_masked(value) or not np.isfinite(value):raise ValueError('Checkpoint has no observed surface height in its own source')
                row[field+'_z']=float(value)
        result.append(row)
    return result


def validate(run,config):
    """Require predeclared held-out regions, independent from fitted controls."""
    require_stage(run, 'ndsm');require_stage(run, 'lidar')
    if (run/'validation.json').exists():raise ValueError('Validation is already recorded; start a fresh run to change controls or results')
    validation=config.get('validation',{})
    if not all(validation.get(k) for k in ('checkpoints_csv','areas_geojson','buildings_geojson')):
        raise ValueError('Independent validation needs checkpoint CSV, predeclared fit/held_out polygons and real building footprints')
    proof=read_json(run/'proof.json')
    if not proof.get('validation_areas_sha256') or sha256(validation['areas_geojson'])!=proof['validation_areas_sha256']:
        raise ValueError('Fit/held-out areas must be declared before frame selection and remain unchanged')
    with safe_path(validation['checkpoints_csv']).open() as stream: rows=list(csv.DictReader(stream))
    areas=read_json(validation['areas_geojson'])['features']
    regions=validation_regions(areas,validation.get('geometry_crs','EPSG:4326'),config['output_crs'],rows)
    rows=checkpoint_surface_heights(run,rows)
    metrics=checkpoint_validation(rows)
    held_regions=regions['held_out']
    project=Transformer.from_crs(validation.get('buildings_crs','EPSG:4326'),config['output_crs'],always_xy=True).transform
    with rasterio.open(run/'fresh_dsm_0p5m.tif') as ours, rasterio.open(run/'lidar_dsm_0p5m.tif') as lidar:
        ref=lidar.read(1,masked=True);source=ours.read(1,masked=True).filled(np.nan);aligned=np.full(lidar.shape,np.nan,dtype='float32')
        dx,dy=metrics['fitted_xy_shift_m'];offset=metrics['fitted_vertical_offset_m']
        shifted=ours.transform*rasterio.Affine.translation(dx/ours.transform.a,dy/ours.transform.e)
        reproject(source,aligned,src_transform=shifted,src_crs=ours.crs,src_nodata=np.nan,dst_transform=lidar.transform,dst_crs=lidar.crs,dst_nodata=np.nan,resampling=Resampling.bilinear)
        held=geometry_mask([mapping(g) for g in held_regions],out_shape=lidar.shape,transform=lidar.transform,invert=True)
        valid=held & np.isfinite(aligned) & ~np.ma.getmaskarray(ref)
        residual=aligned[valid]+offset-ref.data[valid]
        if not len(residual):raise ValueError('No comparable surface cells in the independent held-out areas')
        metrics.update(surface_nmad_m=nmad(residual),surface_rmse_m=float(np.sqrt(np.mean(residual**2))),surface_bias_m=float(np.median(residual)),surface_held_out_cells=int(valid.sum()))
    buildings=read_json(validation['buildings_geojson'])['features'];per_building=[]
    with rasterio.open(run/'fresh_ndsm_0p5m.tif') as ours,rasterio.open(run/'lidar_ndsm_0p5m.tif') as lidar:
        source=ours.read(1,masked=True).filled(np.nan);aligned=np.full(lidar.shape,np.nan,dtype='float32')
        shifted=ours.transform*rasterio.Affine.translation(dx/ours.transform.a,dy/ours.transform.e)
        reproject(source,aligned,src_transform=shifted,src_crs=ours.crs,src_nodata=np.nan,dst_transform=lidar.transform,dst_crs=lidar.crs,dst_nodata=np.nan,resampling=Resampling.bilinear)
        reference=lidar.read(1,masked=True)
        for index,feature in enumerate(buildings):
            geometry=transform(project,shape(feature['geometry']))
            if not any(region.contains(geometry) for region in held_regions):continue
            mask=geometry_mask([mapping(geometry)],out_shape=lidar.shape,transform=lidar.transform,invert=True)
            valid=mask & np.isfinite(aligned) & ~np.ma.getmaskarray(reference)
            count=int(valid.sum())
            per_building.append({'id':str(feature.get('properties',{}).get('id',index)),'ours_median_m':float(np.median(aligned[valid])) if count else None,'lidar_median_m':float(np.median(reference.data[valid])) if count else None,'paired_cells':count,'coverage_fraction':float(count/mask.sum()) if mask.any() else 0})
    if not any(b['paired_cells'] for b in per_building):raise ValueError('No real building footprint has paired nDSM coverage in the held-out areas')
    metrics.update(checkpoint_height_source='Sampled from this run: classified ground for fit, DSM for held-out',validation_input_sha256={key:sha256(validation[key]) for key in ('checkpoints_csv','areas_geojson','buildings_geojson')},label=LABEL,run_id=run.name,run_date=read_json(run/'proof.json')['run_date'],validation_date=datetime.now(timezone.utc).isoformat(),vertical_offset_interpretation='Empirical local datum/bias offset, not an assumed geoid conversion',telemetry_vertical_datum=config['telemetry']['vertical_datum'],lidar_vertical_datum=config['lidar']['vertical_datum'],per_building=per_building,video_acquired_at=config['video']['acquired_at'],lidar_acquired_at=config['lidar']['acquired_at'],temporal_gap_days=abs((date(config['video']['acquired_at'])-date(config['lidar']['acquired_at'])).total_seconds())/86400)
    write_json(run/'validation.json',metrics);write_json(METRICS,metrics)
    proof=update(run,'validation',{'path':str(METRICS.relative_to(ROOT)),'held_out':True})
    proof['available']=True;proof['metrics']=str(METRICS.relative_to(ROOT));proof['assets']['validation']=str((run/'validation.json').relative_to(ROOT));write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    return metrics


def shifted_grid(values, transform_grid, dx, dy):
    """Apply only an XY translation; NaN means unobserved, never zero height."""
    rows,columns=np.indices(values.shape,dtype=float)
    return ndimage.map_coordinates(values,[rows-dy/transform_grid.e,columns-dx/transform_grid.a],order=1,mode='constant',cval=np.nan,prefilter=False)


def fit_surface_translation(ours,reference,fit_mask,cell=.5,max_shift_m=10):
    """Ground-only robust translation, evaluated exclusively within frozen fit areas."""
    from scipy.optimize import minimize
    margin=int(math.ceil(max_shift_m/cell))+2
    safe=ndimage.distance_transform_edt(fit_mask)>margin
    # Exclude the whole shift neighbourhood if it reaches missing observations.
    safe &= ndimage.distance_transform_edt(np.isfinite(ours))>margin
    safe &= np.isfinite(reference)
    safe[:margin,:]=False;safe[-margin:,:]=False;safe[:,:margin]=False;safe[:,-margin:]=False
    rows,columns=np.nonzero(safe)
    if len(rows)<100:raise ValueError('Too few independent ground cells with full translation-search support')
    stride=max(1,int(math.ceil(len(rows)/15000)));rows=rows[::stride];columns=columns[::stride];target=reference[rows,columns]
    def differences(shift):
        dx,dy=shift
        return ndimage.map_coordinates(ours,[rows+dy/cell,columns-dx/cell],order=1,mode='constant',cval=np.nan,prefilter=False)-target
    def objective(shift):
        residual=differences(shift)
        if not np.isfinite(residual).all():return 1e12
        residual-=np.median(residual)
        # Trim the largest 10% only for fitting, never for held-out evaluation.
        squared=np.sort(residual*residual);return float(np.mean(squared[:max(1,int(len(squared)*.9))]))
    candidates=[(objective((dx,dy)),dx,dy) for dx in np.linspace(-max_shift_m,max_shift_m,11) for dy in np.linspace(-max_shift_m,max_shift_m,11)]
    _,dx,dy=min(candidates)
    result=minimize(objective,[dx,dy],method='Nelder-Mead',bounds=[(-max_shift_m,max_shift_m)]*2,options={'xatol':.01,'fatol':1e-9,'maxiter':200,'initial_simplex':[[dx,dy],[dx+.25,dy],[dx,dy+.25]]})
    dx,dy=map(float,result.x);residual=differences((dx,dy));offset=float(-np.median(residual))
    f0=objective((dx,dy));step=.5
    hxx=(objective((dx+step,dy))-2*f0+objective((dx-step,dy)))/step**2
    hyy=(objective((dx,dy+step))-2*f0+objective((dx,dy-step)))/step**2
    hxy=(objective((dx+step,dy+step))-objective((dx+step,dy-step))-objective((dx-step,dy+step))+objective((dx-step,dy-step)))/(4*step**2)
    eigenvalues=np.linalg.eigvalsh([[hxx,hxy],[hxy,hyy]])
    return {'fitted_xy_shift_m':[dx,dy],'fitted_vertical_offset_m':offset,'fit_count':len(rows),'fit_objective_m2':f0,'fit_search_bound_m':max_shift_m,'fit_objective_curvature':eigenvalues.tolist(),'fit_at_search_boundary':bool(max(abs(dx),abs(dy))>max_shift_m-.25),'fit_ground_nmad_m':nmad(residual),'fit_method':'Ground-height translation, robust demeaned residual search. Only frozen fit cells with full search support used; largest 10% residuals trimmed in fitting only.'}


def held_out_patch_matches(ours,reference,held,grid_transform,shift):
    """Diagnostic local ground correspondences; never used to correct either grid."""
    from scipy.optimize import minimize
    cell=abs(grid_transform.a);radius=int(round(8/cell));search=3.;spacing=int(round(20/cell))
    support=ndimage.distance_transform_edt(held)>radius+int(math.ceil(search/cell))+2
    aligned=shifted_grid(ours,grid_transform,*shift);support &= ndimage.distance_transform_edt(np.isfinite(aligned)&np.isfinite(reference))>radius+int(math.ceil(search/cell))+2
    points=[]
    for row in range(radius+1,ours.shape[0]-radius-1,spacing):
        for column in range(radius+1,ours.shape[1]-radius-1,spacing):
            if not support[row,column]:continue
            rr,cc=np.mgrid[row-radius:row+radius+1:2,column-radius:column+radius+1:2];target=reference[rr,cc]
            design=np.column_stack((rr.ravel()-row,cc.ravel()-column,np.ones(rr.size)))
            detrended=target.ravel()-design@np.linalg.lstsq(design,target.ravel(),rcond=None)[0]
            if np.std(detrended)<.03:continue
            def objective(delta):
                values=ndimage.map_coordinates(aligned,[rr+delta[1]/cell,cc-delta[0]/cell],order=1,mode='constant',cval=np.nan,prefilter=False)
                if not np.isfinite(values).all():return 1e12
                residual=values-target;residual-=np.mean(residual);return float(np.mean(residual*residual))
            initial=min((objective((dx,dy)),dx,dy) for dx in (-2.,0.,2.) for dy in (-2.,0.,2.))
            fit=minimize(objective,initial[1:],method='Nelder-Mead',bounds=[(-search,search)]*2,options={'xatol':.025,'fatol':1e-8,'maxiter':120,'initial_simplex':[[initial[1],initial[2]],[initial[1]+.25,initial[2]],[initial[1],initial[2]+.25]]})
            dx,dy=map(float,fit.x);base=objective((dx,dy));h=.5
            hxx=(objective((dx+h,dy))-2*base+objective((dx-h,dy)))/h**2;hyy=(objective((dx,dy+h))-2*base+objective((dx,dy-h)))/h**2
            hxy=(objective((dx+h,dy+h))-objective((dx+h,dy-h))-objective((dx-h,dy+h))+objective((dx-h,dy-h)))/(4*h*h)
            curvature=np.linalg.eigvalsh([[hxx,hxy],[hxy,hyy]])
            if max(abs(dx),abs(dy))>=search-.25 or curvature.min()<.0001:continue
            x,y=grid_transform*(column+.5,row+.5)
            points.append({'lidar_xy':[x,y],'ours_aligned_xy':[x-dx,y-dy],'local_shift_m':[dx,dy],'patch_rmse_after_local_offset_m':float(math.sqrt(base)),'minimum_curvature':float(curvature.min())})
    return points


def validate_surfaces(run,config):
    """Independent surface validation with a frozen spatial split and no external controls."""
    require_stage(run,'ndsm');require_stage(run,'lidar')
    if (run/'validation.json').exists():raise ValueError('Validation already recorded; start a fresh run')
    proof=read_json(run/'proof.json');validation=config['validation']
    if sha256(validation['areas_geojson'])!=proof.get('validation_areas_sha256'):raise ValueError('Validation areas changed since original image selection')
    areas=read_json(validation['areas_geojson'])['features']
    regions=validation_regions(areas,validation['geometry_crs'],config['output_crs'],[])
    arrays={};profile=None
    for prefix in ('fresh','lidar'):
        for kind in ('ground','dsm','ndsm'):
            with rasterio.open(run/f'{prefix}_{kind}_0p5m.tif') as raster:
                if profile and (raster.shape!=arrays['fresh_ground'].shape or raster.transform!=profile['transform']):raise ValueError('Validation grid mismatch')
                if profile is None:profile=raster.profile
                arrays[prefix+'_'+kind]=raster.read(1,masked=True).filled(np.nan)
    shape_grid=arrays['fresh_ground'].shape;grid_transform=profile['transform']
    masks={key:geometry_mask([mapping(g) for g in geometries],out_shape=shape_grid,transform=grid_transform,invert=True) for key,geometries in regions.items()}
    # Ground stability is observed independently in both source nDSMs; vegetation/buildings are excluded from fitting.
    ground_observed=(np.abs(arrays['fresh_ndsm'])<.5)&(np.abs(arrays['lidar_ndsm'])<.5)
    ours=arrays['fresh_ground'].copy();ours[~ground_observed]=np.nan
    reference=arrays['lidar_ground'].copy();reference[~ground_observed]=np.nan
    # Candidate movements must remain within the spatial split. Terrain interpolation is documented separately.
    metrics=fit_surface_translation(ours,reference,masks['fit'],abs(grid_transform.a))
    dx,dy=metrics['fitted_xy_shift_m'];offset=metrics['fitted_vertical_offset_m']
    aligned=shifted_grid(arrays['fresh_dsm'],grid_transform,dx,dy)
    held=masks['held_out']&np.isfinite(aligned)&np.isfinite(arrays['lidar_dsm'])
    residual=aligned[held]+offset-arrays['lidar_dsm'][held]
    if len(residual)<100:raise ValueError('Insufficient held-out surface overlap for independent validation')
    rr,cc=np.nonzero(held);xx=grid_transform.c+(cc+.5)*grid_transform.a;yy=grid_transform.f+(rr+.5)*grid_transform.e
    xy=np.column_stack((xx-xx.mean(),yy-yy.mean()));plane=np.linalg.lstsq(np.column_stack((xy,np.ones(len(xy)))),residual,rcond=None)[0]
    metrics.update(surface_nmad_m=nmad(residual),surface_rmse_m=float(np.sqrt(np.mean(residual**2))),surface_bias_m=float(np.median(residual)),surface_held_out_cells=int(held.sum()),held_out_z_nmad_m=nmad(residual),held_out_z_rmse_m=float(np.sqrt(np.mean(residual**2))),tilt_m_per_km=(plane[:2]*1000).tolist(),tilt_deg=float(math.degrees(math.atan(np.linalg.norm(plane[:2])))))
    matches=held_out_patch_matches(arrays['fresh_ground'],arrays['lidar_ground'],masks['held_out'],grid_transform,(dx,dy))
    metrics.update(held_out_count=len(matches),held_out_xy_rmse_m=None,scale_error_percent=None,rotation_error_deg=None)
    limitations=['Images are from 2016 and LiDAR project acquisition spans 2019–2020. Surface differences include real change and seasonal vegetation, not only measurement error.','EXIF altitudes have no confirmed geoid model; the fitted local vertical offset is an empirical bias/datum correction.','Horizontal diagnostics use independent held-out terrain patches, not surveyed ground control.']
    if len(matches)>=4:
        a=np.array([p['ours_aligned_xy'] for p in matches]);b=np.array([p['lidar_xy'] for p in matches]);scale,rotation,_=similarity(a,b)
        metrics.update(held_out_xy_rmse_m=float(np.sqrt(np.mean(np.sum((a-b)**2,axis=1)))),scale_error_percent=float((1/scale-1)*100),rotation_error_deg=float(-math.degrees(math.atan2(rotation[1,0],rotation[0,0]))))
    else:limitations.append('Too few unambiguous held-out ground patches to measure scale/rotation independently; those metrics remain unavailable.')
    if metrics['fit_at_search_boundary']:limitations.append('Fitted XY translation reached the search boundary; horizontal fit needs review.')
    if min(metrics['fit_objective_curvature'])<.0001:limitations.append('Terrain offers weak XY registration constraints; fitted translation is poorly identifiable.')
    per_building=[];project=Transformer.from_crs(validation.get('buildings_crs','EPSG:4326'),config['output_crs'],always_xy=True).transform
    aligned_ndsm=shifted_grid(arrays['fresh_ndsm'],grid_transform,dx,dy)
    for index,feature in enumerate(read_json(validation['buildings_geojson'])['features']):
        geometry=transform(project,shape(feature['geometry']))
        if not any(region.contains(geometry) for region in regions['held_out']):continue
        mask=geometry_mask([mapping(geometry)],out_shape=shape_grid,transform=grid_transform,invert=True)
        valid=mask&np.isfinite(aligned_ndsm)&np.isfinite(arrays['lidar_ndsm']);count=int(valid.sum())
        per_building.append({'id':str(feature.get('properties',{}).get('id',index)),'source_url':feature.get('properties',{}).get('source_url'),'ours_median_m':float(np.median(aligned_ndsm[valid])) if count else None,'lidar_median_m':float(np.median(arrays['lidar_ndsm'][valid])) if count else None,'paired_cells':count,'coverage_fraction':float(count/mask.sum()) if mask.any() else 0})
    if not any(b['paired_cells'] for b in per_building):limitations.append('No mapped held-out building has paired source coverage; per-building height comparison remains unavailable.')
    metrics.update(label=LABEL,run_id=run.name,run_date=proof['run_date'],validation_date=datetime.now(timezone.utc).isoformat(),validation_method='Frozen disjoint spatial regions; fit ground translation only, evaluate full held-out surfaces and separate ground-patch correspondences',validation_input_sha256={key:sha256(validation[key]) for key in ('areas_geojson','buildings_geojson')},correction_policy='Only XY translation and vertical offset fitted from the fit region. Scale, rotation and tilt are independent held-out diagnostics and are never corrected away.',vertical_offset_interpretation='Empirical local datum/bias offset, not an assumed geoid conversion',image_acquired_at=config['image_collection']['acquired_at'],lidar_acquired_at=config['lidar']['acquired_at'],lidar_acquisition_range=config['lidar'].get('acquisition_range'),image_vertical_datum=config['image_collection']['vertical_datum'],lidar_vertical_datum=config['lidar']['vertical_datum'],temporal_gap_days=None,per_building=per_building,held_out_ground_matches=matches,limitations=limitations,all_requested_diagnostics_available=bool(len(matches)>=4 and any(b['paired_cells'] for b in per_building)))
    write_json(run/'validation.json',metrics);write_json(METRICS,metrics)
    proof=update(run,'validation',{'path':str(METRICS.relative_to(ROOT)),'held_out':True,'all_requested_diagnostics_available':metrics['all_requested_diagnostics_available']})
    proof['available']=True;proof['metrics']=str(METRICS.relative_to(ROOT));proof['assets']['validation']=str((run/'validation.json').relative_to(ROOT));write_json(run/'proof.json',proof);write_json(BASE/'status.json',proof)
    return metrics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('status','frames','images','sparse','align','dense','rasterize','validate','preview'))
    parser.add_argument('--manifest',default=str(MANIFEST));parser.add_argument('--run')
    args=parser.parse_args()
    try:
        if args.stage=='status':print(json.dumps(get_status(args.manifest),indent=2));return
        config=need_sources(args.manifest)
        if args.stage=='frames':print(select_frames(config,args.manifest));return
        if args.stage=='images':print(select_images(config));return
        if not args.run:raise ValueError('--run is required; prior products are never selected automatically')
        run,recorded=load_run(args.run)
        if recorded!=config:raise ValueError('Manifest changed; start a fresh run')
        {'sparse':sparse,'align':align_gps,'dense':dense,'rasterize':rasterize,'preview':export_dense_preview,'validate':validate_surfaces if config.get('image_collection') else validate}[args.stage](run,config)
    except (ValueError,KeyError,TypeError,OSError,RuntimeError,subprocess.CalledProcessError) as exc:
        parser.exit(2,str(exc)+'\n')


if __name__=='__main__':main()
