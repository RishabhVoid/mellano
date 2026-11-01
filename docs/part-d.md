# Disaster → Impact → Response

The active product is a local matte Three.js diorama using real FABDEM ground, OSM context and OSM/Google footprints and heights. Satellite terrain/roof texturing was removed from the active UI at the user's request. No environment-file read is needed to start the viewer or API.

## Use

Choose one of the five hazard modules, choose a preset or click the required real footprint/embankment, then Run. The job is deterministic and stored under `runs/product/product_<hash>/`. Inspect Impact and Response using the same play/scrub/speed timeline. Advanced retains source notes, layers, exaggeration and Drone proof. July 2023 stays disabled until real radar masks and calibrated static replay exist.

`make product` launches a duplicate-protected background precompute. `logs/STATUS.md` records status and links each job log. The worker uses nice15, numerical thread limits2, and a global compute lock. API requests and precompute share that lock. Existing matching flood outputs are reused; missing or changed data triggers a new content-identified run.

## Scientific boundaries

All counterfactual modules say **Hypothetical, not a forecast**. Flood is the fixed-step local-inertial/virtual-pipe approximation with a river surface proxy from FABDEM inside OSM polygons; no observed bathymetry or drainage network is invented. Breach edits mapped embankment cells and need not cause a visible flood difference. Quake damage and wind/gap fire spread are explicitly illustrative heuristics on real footprints, not validated structural/fire predictions. Chain combines quake damage/debris and subsequent flood computation; its colour bands combine hazards and are not pure water-depth labels.

Ground/display arrays are sampled at40m for the product viewer; impact calculations use the10m analysis grid. Heights remain native30m FABDEM information resampled to10m. Building states are Uint8 `[severity,state]` pairs in the exact exported footprint-part order; manifest SHA256/count prevents mismatched geometry. Counts deduplicate source/feature IDs, so205642 rendered parts represent205605 building entities. Unknown heights remain unknown for height-dependent damage.

Impact metrics describe affected land outside mapped normal water, building exposure bands, modelled physical road-segment closures, and affected residential population only when real WorldPop2020 raster coverage exists. WorldPop is a population estimate, not live occupancy or casualties. Missing coverage is null, not zero. Flood building exposure samples nearby exterior water, not indoor depth.

## Response

Facilities are real OSM hospitals, clinics, fire stations, police and schools. Schools are potential relief sites, never confirmed camps. Directed graph edges use shared OSM nodes, OSM speeds or declared class defaults, and hazard closure sampling. Grade-separated deck/tunnel heights are unknown; depth-based closures can be wrong. Cut-off means disconnected from the chosen model road component, not observed isolation.

The demonstration fleet has at most one assumed unit per type; availability, staffing and capacity are unknown. Dispatch uses shortest modelled travel times, greedy assignment and pairwise improvement; it is not a global optimum or operational evacuation advice. Current unit positions are advanced between saved frames before rerouting. Boats use connected wet cells, a modelled dry-bank launch and explicit access/draft/speed assumptions. Access connectors are bounded and hazard-checked.

CC0 Kenney Car Kit and Watercraft Kit models are served locally, with LICENSE files, original archive and model hashes in `web/public/assets/vehicles/manifest.json`. Vehicle dimensions, hover offset and overview scaling are display conventions, not measured Delhi assets.

## API and files

- `GET /product/catalog`: modules, presets, readiness, building index and published runs.
- `POST /product/run`: validates scenario, admits/reuses a run, returns durable status and manifest URLs.
- `GET /product/runs/{id}/status`: queued/running/complete/failed plus stage and progress.
- `/data/product/runs/{id}/manifest.json`: frame URLs, provenance, limitations, resource policy and measured runtime.
- `metrics.json`: documented maximum-frame summary; `metrics_0000.json` etc: exact per-frame results.
- `response_0000.json` etc: modelled station/road/hotspot/unit paths and ETAs; `response.json`: final frame.
- `GET /gemini/status`: recorded readiness, no paid request.
- `POST /product/runs/{id}/briefing`: explicit bounded Gemini call grounded in that frame's computed JSON files, never an input to a solver. Credentials remain server-side.

## Verification and recordings

`make check build` includes numerical, topology, admission, source-contract, Gemini boundary and TypeScript checks. Browser verification must additionally check real geometry, five colours, timeline updates, vehicle routes and missing-data labels. `?product_run=<id>&step=2&frame=30` loads a saved result without starting a job.

Scenes `01_intro`, `03_breach_response`, `06_quake_response`, and `07_chain` drive camera, timeline, layers and burned-in labels. Append `&record=1` for1080p canvas capture;30fps is requested, not asserted without inspecting the actual recording. The historical `02_replay2023` scene refuses to record without real calibrated replay data. No fake flood outline or score is used to fill it.
