# Otec

Otec is a live height map of Delhi for disasters. A base 3D model of the city is built from free satellite height data. We replay the real July 2023 Yamuna flood on it and score it against real satellite flood maps. Then we run "what if" scenarios (embankment breach, debris, heavier river). A drone pass refreshes changed tiles and the sim reruns on the fresh ground.

## Team protocol (Meko, required)
- Before every task: recall from the Meko datapack `otec-build` (memory_search / context_search) for related decisions.
- After every task: save a short summary of what was done and why (memory_add).
- Every decision that affects teammates (data source, datum offset, file format, API shape): save it and name who made it. It will be promoted to shared knowledge.
- Never write API keys, tokens or personal data to Meko.

## Rules
- Honesty: never fake data. Simulated things must be labelled "simulated" in the UI and in recordings. Real data must show its source.
- Every number shown in the UI must come from a computed file in `data/derived/` or `runs/`, never hard-coded.
- Never generate placeholder flood masks or water. Codex acquires real Sentinel-1 sources in background jobs; missing inputs remain visibly unavailable. Explicitly requested OSM/FABDEM-relative experiments are allowed only with the label "Hypothetical, not a forecast" and no satellite-validation claim.
- Keep the sim deterministic: same inputs and seed give the same output.
- The demo machine is a Mac (Apple Silicon). Everything must run on macOS without CUDA. Heavy steps must also be pre-computable and recordable.

## Stack
- Data prep and sim: Python 3.11, numpy, scipy, rasterio, geopandas, shapely, pyproj, requests, fastapi, uvicorn.
- Viewer: Vite + TypeScript + three.js. AOI diorama from exported local grids, with optional display-only Mapbox Satellite LOD imagery and an offline matte fallback; no globe or ion token.
- Recording: in-page MediaRecorder for quick takes. Playwright + ffmpeg for frame-by-frame smooth renders.

## Layout
data/raw/        downloaded inputs (gitignored)
data/derived/    grids, masks, stage tables (small ones committed)
sim/             python sim core + CLI + FastAPI
web/             Three.js AOI diorama
scripts/         download, record, convert
runs/            sim outputs per scenario (depth rasters, metrics.json)
recordings/      mp4, webm, png
