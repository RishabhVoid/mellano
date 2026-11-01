"""Current-incident entry and grounded recommendations; no automatic dispatch."""
from collections import Counter
import json
from pathlib import Path
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from sim import areas, gemini_api
from sim.config import DERIVED, WEB_DATA
from sim.decision_support import HumanReport, assess_situation, build_decision_context, fallback_recommendations, generate_recommendations

router=APIRouter(prefix='/situation',tags=['situation'])


class SituationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    area_id:str=Field(pattern=r'^area_[0-9a-f]{20}$')
    report:HumanReport|None=None
    run_id:str|None=Field(default=None,pattern=r'^product_[0-9a-f]{16}$')
    frame_index:int=Field(default=0,ge=0,le=10000)


def current_observations(area_id):
    path=DERIVED/'current_observations.json'
    if not path.is_file():return []
    # Only server-owned records can provide verified observations.
    document=gemini_api.read_document(path,128_000)
    return document.get('areas',{}).get(area_id,[])


def selected_area(area_id):
    try:
        value=areas.public_status(areas.get_store().latest(area_id))
    except (ValueError,KeyError):
        raise HTTPException(404,'Select an area first') from None
    if value['status']!='ready':
        raise HTTPException(409,'This area is queued for a model server; no ready model is available yet')
    return value['descriptor']


def response_evidence(descriptor):
    url=descriptor.get('assets',{}).get('response_context_url')
    if not isinstance(url,str) or not url.startswith('/data/') or '..' in Path(url).parts or '.env' in url.lower():
        return {},[]
    context=gemini_api.read_document(WEB_DATA/url.removeprefix('/data/'),2_000_000)
    left,top,right,bottom=descriptor['clip_bounds_local_m']
    stations=[]
    for station in context.get('stations',[]):
        position=station.get('position')
        if not isinstance(position,list) or len(position)!=2:continue
        x,z=position
        if left<=x<=right and top<=z<=bottom:
            stations.append({key:station.get(key) for key in ('id','kind','name','position','source_url','nearest_node_distance_m')})
    for station in stations:
        station.update(availability='unknown',status='unverified',designated_relief_site=False)
    return {**context,'stations':stations,'coordinate_reference':descriptor.get('terrain',{})},stations


def evidence(request):
    descriptor=selected_area(request.area_id)
    try:
        situation=assess_situation(request.report, trusted_observations=current_observations(request.area_id))
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    context,stations=response_evidence(descriptor)
    run_context=None;refs=[]
    if request.run_id and descriptor['metrics_scope']=='full_cached_aoi':
        encoded,refs=gemini_api.briefing_context(request.run_id,request.frame_index)
        run_context=json.loads(encoded)
    scope={'bbox':descriptor['requested_bbox'],'simulation_metrics_scope':descriptor['metrics_scope'],'simulation_metrics_bbox':descriptor.get('metrics_bbox'),
           'run_omitted':bool(request.run_id and run_context is None)}
    context_url=descriptor.get('assets',{}).get('response_context_url')
    if context_url and context: refs.append(context_url)
    packet=build_decision_context(situation,run_context,refs,context,scope)
    return descriptor,situation,stations,packet


@router.post('/assess')
def assess(request:SituationRequest):
    descriptor,situation,stations,packet=evidence(request)
    checklist=fallback_recommendations(packet)
    return {'area_id':request.area_id,'descriptor':descriptor,'situation':situation,'stations':stations,
            'station_counts':dict(Counter(s['kind'] for s in stations)),'data_scope':packet['area_scope'],
            'missing':checklist['known_missing_data'],'checklist':checklist,
            'facility_label':'Real: OpenStreetMap facilities · availability and access unverified'}


@router.post('/recommend')
def recommend(request:SituationRequest):
    descriptor,situation,stations,packet=evidence(request)
    if not gemini_api._gate.acquire(blocking=False):
        raise HTTPException(429,'A Gemini request is already running')
    try:
        now=time.monotonic()
        if now-gemini_api._last_request<15:
            raise HTTPException(429,'Wait 15 seconds between Gemini requests')
        gemini_api._last_request=now
        result=generate_recommendations(packet)
        return {**result,'area_id':request.area_id,'data_scope':packet['area_scope']}
    finally:
        gemini_api._gate.release()
