"""Explicit server-only regional builder: registered verified FABDEM + real OSM.

Run through sim.areas worker with --source-tile. No package download, watcher,
shared config mutation or environment-file loading occurs here.
"""
import argparse
import math
import os
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).absolute().parents[1]))
for _name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[_name]='2'
import numpy as np
import requests
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject,Resampling,transform_bounds
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform
from sim.areas import read_json,write_json,safe_path,digest,selection
from sim.data import osm_geometry
from sim.regions import pack_for_bbox

OVERPASS=('https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter')
ROAD_WIDTHS={'motorway':18,'trunk':16,'primary':12,'secondary':9,'tertiary':7,'residential':5,'unclassified':5,'service':3,'motorway_link':8,'trunk_link':7,'primary_link':6,'secondary_link':5,'tertiary_link':4}


def mapped_height(tags):
    value=str(tags.get('height','')).strip().lower();match=re.fullmatch(r'([0-9]+(?:[.,][0-9]+)?)\s*(m|metres|meters|ft|feet|\')?',value)
    if match:
        height=float(match[1].replace(',','.'))*(.3048 if match[2] in ('ft','feet',"'") else 1)
        if 0<height<=500:return height,'OSM explicit height'
    return None,'Height not mapped; footprint only'


def fetch_osm(bbox):
    west,south,east,north=bbox;extent=f'{south},{west},{north},{east}'
    tags=['["building"]','["natural"="water"]','["waterway"="riverbank"]','["water"="river"]','["natural"="wood"]','["landuse"~"^(forest|grass)$"]','["leisure"="park"]','["man_made"="dyke"]','["embankment"="yes"]']
    clauses=''.join(f'{kind}{tag}({extent});' for tag in tags for kind in ('way','relation'))+f'way["highway"]({extent});nwr["amenity"~"^(hospital|clinic|fire_station|police|school)$"]({extent});'
    query=f'[out:json][timeout:180];({clauses});out geom;'
    for endpoint in OVERPASS:
        try:
            response=requests.get(endpoint,params={'data':query},headers={'User-Agent':'Otec explicit regional server builder','Accept':'application/json'},timeout=(20,240));response.raise_for_status();data=response.json()
            if not isinstance(data.get('elements'),list) or data.get('remark'):raise ValueError('Overpass returned incomplete data')
            data['otec_source']=endpoint;return data
        except (requests.RequestException,ValueError):continue
    raise RuntimeError('Public OSM acquisition failed; no empty replacement data was created')


def assemble(config,source_tile,osm,output):
    """Purely scoped input/output builder; unit fixtures can supply their own raster."""
    region=selection({'bbox':config['bbox']})
    pack=pack_for_bbox(region['bbox'])
    if pack is None:raise ValueError('Another verified FABDEM source pack is required for this area')
    source_tile=safe_path(source_tile);output=safe_path(output);output.mkdir(parents=True,exist_ok=True)
    if digest(source_tile)!=pack['sha256']:raise ValueError('Source raster differs from the verified FABDEM source pack')
    crs=region['suggested_crs'];cell=10.;west,south,east,north=transform_bounds('EPSG:4326',crs,*region['bbox'],densify_pts=21)
    west=math.floor(west/cell)*cell;south=math.floor(south/cell)*cell;east=math.ceil(east/cell)*cell;north=math.ceil(north/cell)*cell;width=round((east-west)/cell);height=round((north-south)/cell);affine=from_origin(west,north,cell,cell)
    ground=np.full((height,width),np.nan,dtype='float32')
    with rasterio.open(source_tile) as source:
        if source.crs is None:raise ValueError('Source raster CRS is missing')
        reproject(rasterio.band(source,1),ground,src_transform=source.transform,src_crs=source.crs,src_nodata=source.nodata,dst_transform=affine,dst_crs=crs,dst_nodata=np.nan,resampling=Resampling.bilinear,num_threads=2)
    if not np.isfinite(ground).all():raise ValueError('Source pack does not cover the full requested projected grid; no heights were invented')
    ground.astype('<f4').tofile(output/'terrain.bin');origin=[west,north];project=Transformer.from_crs(4326,crs,always_xy=True).transform;clip=box(*region['bbox'])
    def local(geometry):return transform(lambda x,y,z=None:(x-west,north-y),transform(project,geometry))
    footprints=[];roads=[];banks=[];water=[];green=[]
    members={m['ref'] for item in osm['elements'] if item.get('type')=='relation' and item.get('tags',{}).get('building') for m in item.get('members',[]) if m.get('type')=='way'}
    for item in osm['elements']:
        tags=item.get('tags',{});identity=f"{item.get('type')}/{item.get('id')}";is_building=tags.get('building') not in (None,'no') and not (item.get('type')=='way' and item.get('id') in members)
        if is_building or tags.get('natural') in ('water','wood') or tags.get('water')=='river' or tags.get('waterway')=='riverbank' or tags.get('landuse') in ('forest','grass') or tags.get('leisure')=='park':
            geometry=osm_geometry(item,polygon=True)
            if geometry is not None and not geometry.is_empty:
                geometry=geometry.intersection(clip);parts=list(geometry.geoms) if hasattr(geometry,'geoms') else [geometry]
                for polygon in parts:
                    if polygon.geom_type!='Polygon' or polygon.is_empty:continue
                    if is_building:
                        shape=local(polygon);point=shape.representative_point();height_m,height_source=mapped_height(tags);row=min(height-1,max(0,int(point.y/cell)));column=min(width-1,max(0,int(point.x/cell)))
                        footprints.append({'id':identity,'footprint_source':'OSM','footprint':[[[round(x,2),round(y,2)] for x,y in ring.coords] for ring in [shape.exterior,*shape.interiors]],'height':height_m,'height_source':height_source,'ground_height_m':float(ground[row,column])})
                    if tags.get('natural')=='water' or tags.get('water')=='river' or tags.get('waterway')=='riverbank':water.append(transform(project,polygon))
                    if tags.get('natural')=='wood' or tags.get('landuse') in ('forest','grass') or tags.get('leisure')=='park':green.append(transform(project,polygon))
        if item.get('type')=='way' and (tags.get('highway') in ROAD_WIDTHS or tags.get('man_made')=='dyke' or tags.get('embankment')=='yes'):
            geometry=osm_geometry(item,polygon=False)
            if geometry is None:continue
            geometry=geometry.intersection(clip);parts=list(geometry.geoms) if hasattr(geometry,'geoms') else [geometry]
            for line in parts:
                if line.geom_type!='LineString' or line.is_empty:continue
                points=[[round(x,2),round(y,2)] for x,y in local(line).coords]
                if tags.get('highway') in ROAD_WIDTHS and tags.get('tunnel') not in ('yes','building_passage'):roads.append({'osm_id':identity,'name':tags.get('name'),'highway':tags['highway'],'bridge':tags.get('bridge') not in (None,'no'),'tunnel':False,'width_m':ROAD_WIDTHS[tags['highway']],'width_source':'Display width by mapped highway class; not surveyed road width','points':points})
                if tags.get('man_made')=='dyke' or tags.get('embankment')=='yes':banks.append(points)
    cover=np.zeros(ground.shape,'uint8')
    for geometries,bit in ((water,1),(green,2)):
        if geometries:cover|=rasterize([(g,bit) for g in geometries],out_shape=ground.shape,transform=affine,dtype='uint8')
    cover.tofile(output/'landcover.bin')
    source={'source':'FABDEM V1-2','source_url':pack['source_url'],'license':'CC BY-NC-SA4.0','native_resolution_m':30,'resampling':'Bilinear to10m; adds no native elevation detail','vertical_datum':'EGM2008','sha256':pack['sha256']}
    osm_source={'source':'OpenStreetMap contributors','source_url':osm.get('otec_source',OVERPASS[0]),'license':'ODbL','timestamp':osm.get('osm3s',{}).get('timestamp_osm_base')}
    write_json(output/'terrain.json',{'version':1,'width':width,'height':height,'cell_size_m':cell,'origin':origin,'crs':crs,'min_height_m':float(ground.min()),'max_height_m':float(ground.max()),'extent_m':[width*cell,height*cell],'dtype':'float32','byte_order':'little-endian','order':'row-major north-to-south','sample_location':'pixel centres; origin is northwest corner','local_axes':['east metres','south metres'],'binary_url':'terrain.bin','render_stride':3,'source':{'aoi':region['bbox'],'terrain':source,'osm':osm_source}})
    write_json(output/'buildings.json',{'origin':origin,'crs':crs,'local_axes':['east metres','south metres'],'source':'OpenStreetMap contributors · ODbL','summary':{'footprint_parts':len(footprints),'height_known':sum(b['height'] is not None for b in footprints),'height_missing':sum(b['height'] is None for b in footprints),'google_open_buildings_available':False},'buildings':footprints})
    write_json(output/'context.json',{'source':'OpenStreetMap contributors · ODbL','water_label':'Real: OpenStreetMap river outline','landcover_url':'landcover.bin','landcover_bits':{'water':1,'green':2},'water_cells':int(np.count_nonzero(cover&1)),'green_cells':int(np.count_nonzero(cover&2)),'roads':roads,'provenance':osm_source})
    write_json(output/'embankments.json',{'source':'OpenStreetMap mapped dykes / embankments; no surveyed crest claim','count':len(banks),'lines':banks})
    from sim.response_context import build_graph,build_stations
    graph=build_graph(osm['elements'],origin,[0,0,width*cell,height*cell],Transformer.from_crs(4326,crs,always_xy=True));graph['crs']=crs
    stations=build_stations(osm,{'origin':origin,'crs':crs},graph,bbox=region['bbox'])
    write_json(output/'road_graph.json',graph)
    write_json(output/'response_context.json',{'stations':stations,'facilities_status':'mapped_osm_only','population':{'available':False},'provenance':osm_source})
    write_json(output/'area-model.json',{'schema_version':1,'bbox':region['bbox'],'synthetic':False,'provenance':[source,osm_source],'input_data_timestamps':{'osm_snapshot_at':osm_source['timestamp']},'capabilities':{'terrain':True,'buildings':True,'mapped_context':True,'disaster_runs':False,'operational_response':False},'limitations':['OSM footprint coverage is incomplete; no synthetic gap filling.','Only explicit OSM heights are used; unmapped heights remain null and render as footprints.','FABDEM is ground, not surveyed DTM; no Copernicus surface heights used.','No disaster, population, response or incident evidence is generated by this model build.']})
    return {'terrain_cells':width*height,'building_parts':len(footprints),'output':str(output)}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--area-config',required=True);parser.add_argument('--output',required=True);parser.add_argument('--source-tile');args=parser.parse_args();config=read_json(args.area_config);region=selection({'bbox':config['bbox']})
    pack=pack_for_bbox(region['bbox'])
    if pack is None:raise SystemExit('Another verified source pack is required for this area')
    chosen=args.source_tile or pack.get('source_tile')
    if not chosen:raise SystemExit('Supply --source-tile or register a readonly source_tile path for this pack')
    source=safe_path(chosen)
    if not source.is_file() or digest(source)!=pack['sha256']:raise SystemExit('Verified readonly FABDEM source tile is required; no automatic source download')
    osm=fetch_osm(region['bbox']);write_json(safe_path(args.output).parent/'osm-source.json',osm);print(assemble(config,source,osm,args.output))

if __name__=='__main__':main()
