"""Local API and exact-path data refresh. No placeholder flood data."""
import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from sim import drone_proof
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sim.config import RUNS, WEB_DATA
from sim.data import write_json
from sim.export_web import export_all, read_json, run_ids
from sim.relative import CONFIG, MODEL, VERSION, requirements, input_signature, cache_complete
from sim.jobs import launch, state, reconciled_state, serialized
from dataclasses import asdict
from sim.imagery import router as imagery_router
from sim.product_api import router as product_router
from sim.gemini_api import router as gemini_router
from sim.areas_api import router as areas_router
from sim.situation_api import router as situation_router

jobs = {}


async def watch_exports():
    while True:
        try:
            await asyncio.to_thread(export_all)
        except (OSError, ValueError, KeyError):
            pass  # A background job may be writing an export; keep the last valid catalog.
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app):
    watcher = asyncio.create_task(watch_exports()) if os.environ.get("OTEC_WATCH_EXPORTS") == "1" else None
    yield
    if watcher:
        watcher.cancel()
        try: await watcher
        except asyncio.CancelledError: pass


app = FastAPI(title="Otec", version="0.3.0", description="Offline AOI diorama with computed disaster, impact and modelled response", lifespan=lifespan)


app.include_router(imagery_router)
app.include_router(product_router)
app.include_router(gemini_router)
app.include_router(areas_router)
app.include_router(situation_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "otec", "milestone": "disaster-impact-response"}


@app.get("/catalog")
def catalog():
    cached = read_json(WEB_DATA / "catalog.json")
    if cached: return cached
    raise HTTPException(503, "The real data export is not ready; no background build was started.")


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["breach", "debris"]
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=200)
    height_m: float = Field(default=2, ge=0.1, le=30, allow_inf_nan=False)

    @model_validator(mode="after")
    def geometry_valid(self):
        import math
        if self.type == "debris" and len(self.coordinates) < 3: raise ValueError("A debris polygon needs at least three points")
        if any(not math.isfinite(v) or abs(v) > 100000 for point in self.coordinates for v in point): raise ValueError("Coordinates must be finite local metres within the AOI")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_run: Literal["mapped_river"] = "mapped_river"
    edits: list[Edit] = Field(default_factory=list, max_length=10)
    river_plus_m: float = Field(default=1, ge=0, le=3, allow_inf_nan=False)


@app.post("/run", status_code=202)
def start_run(scenario: Scenario):
    missing = requirements()
    if missing: raise HTTPException(409, "Relative-stage inputs are not ready: " + ", ".join(missing))
    payload = scenario.model_dump()
    sources = input_signature()
    digest = hashlib.sha256(json.dumps([VERSION, asdict(CONFIG), payload, sources], sort_keys=True).encode()).hexdigest()[:16]
    run_id = "relative_edit_" + digest
    job_name = "scenario_" + digest
    with serialized("scenario"):
        current = reconciled_state(job_name)
        was_complete = current.get("status") == "complete"
        manifest = read_json(RUNS / run_id / "manifest.json", {}) if was_complete else {}
        if current.get("status") in ("starting", "running") or (was_complete and cache_complete(RUNS / run_id, manifest.get("configuration_hash"))):
            jobs[run_id] = job_name
            return {"run_id": run_id, "model": MODEL}
        for name in state():
            if name.startswith("scenario_") and reconciled_state(name).get("status") in ("starting", "running"):
                raise HTTPException(429, "A custom scenario is running; wait for it to finish.")
        if os.environ.get("OTEC_ENABLE_LOCAL_COMPUTE") != "1":
            raise HTTPException(503, "New simulations require the model server. Cached scenarios remain available; local background computation is off.")
        directory = RUNS / run_id
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "request.json", payload)
        index = read_json(RUNS / "index.json", {"run_ids": []})
        if run_id not in index["run_ids"]: index["run_ids"].append(run_id)
        write_json(RUNS / "index.json", index)
        jobs[run_id] = job_name
        launch(job_name, [sys.executable, "-u", "-m", "sim.relative", "edit", run_id], detail="Computing a hypothetical mapped-river edit", force=was_complete)
    return {"run_id": run_id, "model": MODEL}


def known_run(run_id):
    if not re.fullmatch(r"[a-z0-9_-]{1,80}", run_id) or "env" in run_id or run_id not in [*run_ids(), *jobs]: raise HTTPException(404, "Run not found")


@app.get("/runs/{run_id}/status")
def run_status(run_id: str):
    known_run(run_id)
    if run_id.startswith("relative_edit_"):
        result = reconciled_state("scenario_" + run_id.removeprefix("relative_edit_"))
        if result: return {**result, "error": result.get("detail") if result.get("status") in ("failed", "blocked", "needs_input") else None}
    return {"status": "complete" if (RUNS / run_id / "manifest.json").is_file() else "unavailable"}


@app.get("/runs/{run_id}/frames")
def run_frames(run_id: str):
    known_run(run_id); export_all()
    manifest = read_json(WEB_DATA / "runs" / run_id / "manifest.json")
    if not manifest: raise HTTPException(404, "No computed frames are available")
    return manifest


@app.get("/runs/{run_id}/metrics")
def run_metrics(run_id: str):
    known_run(run_id)
    result = read_json(RUNS / run_id / "metrics.json")
    if result is None: raise HTTPException(404, "No computed metrics are available")
    return result



@app.get("/drone-proof/status")
def drone_status():
    try:
        return drone_proof.get_status()
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Drone source manifest or run status is incomplete")


@app.get("/drone-proof/metrics")
def drone_metrics():
    try:
        return drone_proof.current_metrics()
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(404, "No independently validated metrics for the current drone run")


@app.get("/drone-proof/assets/{asset:path}")
def drone_asset(asset: str):
    try:
        path = drone_proof.asset_path(asset)
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(404, "No computed public drone asset at that path")
    return FileResponse(path, headers={"Cache-Control": "no-store"})


app.mount("/data", StaticFiles(directory=WEB_DATA, check_dir=False), name="data")
