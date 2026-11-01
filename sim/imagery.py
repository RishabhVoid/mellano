"""Display-only imagery: provider fallback, six requests, bounded HTTP disk cache."""
from collections import OrderedDict
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import os
import re
import sqlite3
import threading
import time
import requests
from fastapi import APIRouter, Response
from sim.config import AOI, ROOT

router = APIRouter(prefix="/imagery")
CACHE_DIR = ROOT / "data" / "cache" / "imagery"
PROVIDERS = ("mapbox", "esri")
ESRI_SERVICE = "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer"
ESRI_CREDITS = '<a href="https://www.esri.com/">Esri</a>, Maxar, Earthstar Geographics, and the GIS User Community'
MAX_CONCURRENCY = 6
_lock = threading.RLock()
_metadata_lock = threading.Lock()
_requests = threading.BoundedSemaphore(MAX_CONCURRENCY)
_tiles = OrderedDict()
_key_locks = {}
_MAX_MEMORY_BYTES = 48 * 1024 * 1024
_MAX_DISK_BYTES = 1024 * 1024 * 1024
_states = {provider: {"credits": None, "credits_time": 0, "until": 0, "reason": None, "tile": "untested", "failures": 0} for provider in PROVIDERS}


def token():
    return os.environ.get("VITE_MAPBOX_TOKEN", "").strip()


def provider_token(provider):
    return token() if provider == "mapbox" else os.environ.get("VITE_ARCGIS_KEY", "").strip()


def tile_xy(lon, lat, zoom):
    n = 2 ** zoom
    return int((lon+180)/360*n), int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)


def permitted(zoom, x, y):
    if not 13 <= zoom <= 18: return False
    west,south,east,north = AOI
    x0,y0 = tile_xy(west,north,zoom); x1,y1 = tile_xy(east,south,zoom)
    return x0-1 <= x <= x1+1 and y0-1 <= y <= y1+1


def retry_delay(value=None):
    delay = 60.0
    if isinstance(value,str) and len(value)<=256:
        try: delay = float(value)
        except ValueError:
            try:
                date=parsedate_to_datetime(value)
                if date.tzinfo is None: date=date.replace(tzinfo=timezone.utc)
                delay=date.timestamp()-time.time()
            except (TypeError,ValueError,OverflowError): pass
    if not math.isfinite(delay): delay=60.0
    return max(30,min(300,math.ceil(delay)))


def backoff(provider, reason, retry_after=None):
    with _lock:
        state=_states[provider]; state["failures"]=min(5,state["failures"]+1)
        delay=max(retry_delay(retry_after),min(300,30*2**(state["failures"]-1)))
        state.update(until=max(state["until"],time.monotonic()+delay),reason=reason,tile="backoff")


def availability(provider):
    with _lock: state=_states[provider].copy()
    remaining=max(0,math.ceil(state["until"]-time.monotonic()))
    return {"tile_availability":"backoff" if remaining else "retry_ready" if state["tile"]=="backoff" else state["tile"],
            "backoff_reason":state["reason"] if remaining else None,"retry_after_s":remaining,
            "attribution":state["credits"] or "","available":bool(state["credits"]) and not remaining}


def upstream_failure(provider,response):
    reason="rate_limited" if response.status_code==429 else "access_denied" if response.status_code in (401,403,498,499) else "upstream_unavailable"
    backoff(provider,reason,response.headers.get("Retry-After"))


def ensure_metadata(provider,key):
    if availability(provider)["retry_after_s"]: return
    with _metadata_lock:
        state=_states[provider]
        if availability(provider)["retry_after_s"] or (state["credits"] and time.monotonic()-state["credits_time"]<3600): return
        try:
            url="https://api.mapbox.com/v4/mapbox.satellite.json" if provider=="mapbox" else ESRI_SERVICE
            params={"access_token":key} if provider=="mapbox" else {"token":key,"f":"json"}
            with _requests: response=requests.get(url,params=params,timeout=(4,8))
            if response.status_code!=200: upstream_failure(provider,response); return
            payload=response.json()
            if not isinstance(payload,dict) or payload.get("error"): backoff(provider,"access_denied"); return
            credits=payload.get("attribution") if provider=="mapbox" else payload.get("copyrightText") or ESRI_CREDITS
            if not isinstance(credits,str) or not credits or len(credits)>8192: backoff(provider,"upstream_unavailable"); return
            if provider=="esri" and "href=" not in credits: credits='<a href="https://www.esri.com/">Esri</a> · '+credits
            with _lock: state.update(credits=credits.replace(key,"[redacted]"),credits_time=time.monotonic())
        except (requests.RequestException,ValueError): backoff(provider,"network_unavailable")


@router.get("/status")
def status():
    providers={}
    for provider in PROVIDERS:
        key=provider_token(provider)
        if key:
            ensure_metadata(provider,key); providers[provider]=availability(provider)
        else: providers[provider]={"available":False,"tile_availability":"missing_token","backoff_reason":None,"retry_after_s":0,"attribution":""}
    selected=next((p for p in PROVIDERS if providers[p]["available"]),None)
    configured=[p for p in PROVIDERS if providers[p]["tile_availability"]!="missing_token"]
    next_provider=selected or min(configured,key=lambda p:providers[p]["retry_after_s"],default=None)
    detail=providers[next_provider] if next_provider else {"tile_availability":"missing_token","backoff_reason":None,"retry_after_s":0,"attribution":""}
    reason=("Esri World Imagery fallback ready" if selected=="esri" else "Mapbox Satellite ready") if selected else "Satellite unavailable · matte view; retry cooldown" if configured else "No imagery credentials; matte view is active."
    return {**detail,"available":selected is not None,"provider":selected,"providers":providers,"reason":reason,"display_only":True,
            "maximum_concurrent_requests":MAX_CONCURRENCY,"disk_cache_limit_bytes":_MAX_DISK_BYTES}


@contextmanager
def database():
    # Exact application-owned paths; never follow a cache symlink.
    if any(path.is_symlink() for path in (ROOT/"data",ROOT/"data"/"cache",CACHE_DIR,CACHE_DIR/"tiles.sqlite3")):
        raise OSError("Cache symlink refused")
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(CACHE_DIR/"tiles.sqlite3",timeout=5); connection.row_factory=sqlite3.Row
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS tiles (provider TEXT,z INTEGER,x INTEGER,y INTEGER,content BLOB,media TEXT,expires REAL,etag TEXT,modified TEXT,accessed REAL,size INTEGER,PRIMARY KEY(provider,z,x,y))")
            yield connection
    finally: connection.close()


def remember(cache_key,entry):
    with _lock:
        _tiles[cache_key]=entry; _tiles.move_to_end(cache_key)
        while sum(len(item["content"]) for item in _tiles.values())>_MAX_MEMORY_BYTES: _tiles.popitem(last=False)


def cached_tile(cache_key):
    with _lock:
        cached=_tiles.get(cache_key)
        if cached is not None: _tiles.move_to_end(cache_key); return cached
    try:
        with database() as connection:
            row=connection.execute("SELECT * FROM tiles WHERE provider=? AND z=? AND x=? AND y=?",cache_key).fetchone()
            if row:
                connection.execute("UPDATE tiles SET accessed=? WHERE provider=? AND z=? AND x=? AND y=?",(time.time(),*cache_key))
                value=dict(row); remember(cache_key,value); return value
    except (OSError,sqlite3.Error): pass
    return None


def cache_response(cache_key,data,media,headers,key,old=None):
    control=headers.get("Cache-Control","").lower()
    if "no-store" in control: return {"content":data,"media":media,"expires":time.time(),"no_store":True}
    match=re.search(r"(?:^|,)\s*max-age\s*=\s*(\d+)",control)
    lifetime=min(43200,int(match.group(1))) if match else 3600
    if "no-cache" in control: lifetime=0
    entry={"content":data,"media":media,"expires":time.time()+lifetime,
           "etag":str(headers.get("ETag",(old or {}).get("etag",""))).replace(key,"[redacted]"),
           "modified":str(headers.get("Last-Modified",(old or {}).get("modified",""))).replace(key,"[redacted]")}
    remember(cache_key,entry)
    try:
        with database() as connection:
            connection.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?,?,?,?,?,?,?,?)",(*cache_key,data,media,entry["expires"],entry["etag"],entry["modified"],time.time(),len(data)))
            size=connection.execute("SELECT COALESCE(SUM(size),0) FROM tiles").fetchone()[0]
            while size>_MAX_DISK_BYTES:
                oldest=connection.execute("SELECT rowid,size FROM tiles ORDER BY accessed LIMIT 64").fetchall()
                connection.executemany("DELETE FROM tiles WHERE rowid=?",[(row[0],) for row in oldest]); size-=sum(row[1] for row in oldest)
    except (OSError,sqlite3.Error): pass
    return entry


def tile_response(provider,entry,cached=False):
    remaining=max(0,int(entry["expires"]-time.time()))
    control="no-store" if entry.get("no_store") else f"private, max-age={remaining}"
    return Response(entry["content"],media_type=entry["media"],headers={"Cache-Control":control,"X-Imagery-Provider":provider,"X-Imagery-Cache":"hit" if cached else "miss"})


def provider_tile(provider,key,zoom,x,y):
    cache_key=(provider,zoom,x,y)
    with _lock: guard=_key_locks.setdefault(cache_key,threading.Lock())
    with guard:
        cached=cached_tile(cache_key)
        if cached and cached["expires"]>time.time(): return tile_response(provider,cached,True)
        if availability(provider)["retry_after_s"]: return None
        ensure_metadata(provider,key)
        if not availability(provider)["available"]: return None
        headers={}
        if cached:
            if cached.get("etag"): headers["If-None-Match"]=cached["etag"]
            if cached.get("modified"): headers["If-Modified-Since"]=cached["modified"]
        url=f"https://api.mapbox.com/v4/mapbox.satellite/{zoom}/{x}/{y}@2x.jpg90" if provider=="mapbox" else f"{ESRI_SERVICE}/tile/{zoom}/{y}/{x}"
        params={"access_token":key} if provider=="mapbox" else {"token":key}
        try:
            with _requests:
                if availability(provider)["retry_after_s"]: return None
                response=requests.get(url,params=params,headers=headers,timeout=(4,10))
            if response.status_code==304 and cached:
                entry=cache_response(cache_key,cached["content"],cached["media"],response.headers,key,cached)
            elif response.status_code==200:
                media=response.headers.get("Content-Type","").split(";",1)[0]
                if media not in ("image/jpeg","image/png","image/webp") or not 0<len(response.content)<=2*1024*1024:
                    backoff(provider,"upstream_unavailable"); return None
                entry=cache_response(cache_key,response.content,media,response.headers,key)
            else: upstream_failure(provider,response); return None
            with _lock: _states[provider].update(tile="available",failures=0)
            return tile_response(provider,entry,response.status_code==304)
        except requests.RequestException:
            # Never expose upstream URLs, error bodies or credential-bearing exceptions.
            backoff(provider,"network_unavailable"); return None


@router.get("/tiles/{zoom}/{x}/{y}.jpg")
def tile(zoom:int,x:int,y:int):
    if not permitted(zoom,x,y): return Response(status_code=204,headers={"Cache-Control":"no-store"})
    configured=[]
    for provider in PROVIDERS:
        key=provider_token(provider)
        if not key: continue
        configured.append(provider); response=provider_tile(provider,key,zoom,x,y)
        if response is not None: return response
    delays=[availability(provider)["retry_after_s"] for provider in configured]
    headers={"Cache-Control":"no-store"}
    if delays and min(delays)>0: headers["Retry-After"]=str(min(delays))
    return Response(status_code=204,headers=headers)
