# Otec diorama verification — canvas event workspace

Snapshot: 2026-11-01T01:02:26+00:00. The current scope is a matte canvas workspace with five completed hypothetical event playbacks, impact and modelled response. Area selection, explicit current-incident evidence and human-reviewed recommendations remain available. Heavy background work is stopped. Server deployment is preparation only; no global model service or one-hour completion guarantee is claimed.

## Current checks

The latest integrated full suite ran **200 Python tests: 199 passed, 1 optional Earth Engine skip**, plus **23 passing Node tests**, including three render regressions: **222 passes and one skip overall**. TypeScript and production build passed; the final build transformed 142 modules in 120 ms. The separate cached-event audit passed all five source-current presets: 130 frames, 632 per-run asset references / 628 unique assets including the shared building export. It checked every referenced binary's size and values, building-order SHA256, frame metrics, response counts and route timings in 1.29 s without a model run. Five audit regression tests passed.

The new sidebar selects completed cached events and hides arbitrary uncached settings. Event playback, impact and response are visible in the canvas workflow; existing `?scene=` and `?record=1` hooks are preserved. Browser workflow QA **passed**: all five cached events reached their final saved frame with visible effects and actual metrics (earthquake one simulated minute; fire/flood/riverbank cut five; chain six). Earthquake response shows two modelled units, 12.3-minute average ETA and one unreached hotspot. The real small fire cluster receives a close camera view. Pause/Resume and orbit, rapid Fire→Quake switching followed by Reset, Area opening clearing the hazard to idle, Area close→Fire, all four layer toggles off/on, Info, Drone proof open/back, Fit, zoom and the overhead Top preset were verified. Gemini Explain returned actual provider text and its caption identifies the snapshot time. The Otec QA tab was then closed, returning Chrome for Testing to its New Tab page; the existing user server was left untouched. These are functional observations, not an FPS benchmark.

The final render correction makes severity zero transparent. Flood and breach use the blue computed water mesh without a terrain heatmap; switching does not restore the old heatmap. Browser review confirmed legible paper panels/footer and the blue-water rendering. Scientific metrics and saved scenario files were not changed by these display fixes.

QA reuses the user's existing foreground server PID 64941 at `http://127.0.0.1:5173` and leaves it untouched; no duplicate server, heavy job, download or simulation was started for this change. The job registry reports zero running jobs (remaining entries are complete, stopped or need input). This is a snapshot, not an uptime guarantee. Automatic computation remains disabled. The earlier area-first verification below is retained as historical evidence, and its server-teardown statements do not describe this current user-started process.

The previous area-first suite ran 195 tests (194 passed, one optional skip) in 4.128 s; its production build reported 118 ms. The earlier LOD seam correction passed 14 isolated geometry fixtures and browser review. `make run` was verified as one foreground viewer/API host, health and area catalog returned 200, and a missing JSON asset returned 404. Old separate API/web previews remain stopped.

Browser checks rendered the real distant model and a cached 2 km crop. Cached +3 m opened without computation. An uncached 2 km area at 77.4°E / 28.8°N displayed area build required, no connected server and no completion-time promise. With no report, current-situation assessment stated no confirmed incident. A live Generate priorities request returned Gemini-ranked recommendations labelled for human review; no synthetic incident was submitted. The terrain chunk shadow seams were corrected and checked at the same 2 km centre (77.26003°E,28.65005°N). Dark cross-lines are gone across river/terrain; cut walls are smooth without speckles. No new LOD FPS or GPU-memory result is claimed. The approximately 64 MB source building export still loads into browser RAM, and startup builds initial geometry. LOD reduces rendered geometry; it is not a claim of zero startup processing or fully streamed source data. Deployment files are prepared, not executed.

## Real data preserved

| Data | Verified inventory / interpretation |
|---|---|
| Buildings | 40,195 OSM  + 165,410 Google additions  = 205,605 |
| Footprints with heights | 38,548 OSM  + 153,037 Google  = 191,585 |
| Google 2023 medians | 191,390; remaining 195 heights are 2 explicit OSM + 193 labelled levels×3 m estimates |
| Ground |FABDEM V1-2 N28E077, 30 m resampled 10 m; CC BY-NC-SA 4.0 |
| Surface |Ground + building height exactly once; Copernicus comparison only |
| Population |Real WorldPop 2020 nominal 100 m counts conservatively allocated to 10 m; 96.4957% coverage, 5,335,852.03 modelled residents in valid cells |
| Response facilities | 616 real OSM locations: 131 hospitals, 113 clinics, 15 fire stations, 89 police, 268 schools |
| Road graph | 99,000 nodes / 204,823 directed edges; shared OSM nodes only, no invented bridge intersections |

Population NoData remains unknown. The full-country download was stopped, leaving its full-file checksum unfinished; the independently decoded/re-read/hashed AOI remains valid. Capacity, staffing, fleet and disaster-time facility availability are unknown. Schools are potential relief sites, not designated camps. Simulated units use licensed Kenney CC0 assets.

## Current area and response behavior

Select 1/2/5/10 km by map click or coordinates. Existing real models return cached descriptors and clip the view. Uncached areas become durable server-required requests; no local worker or pretend model starts. Current regional building is limited to the verified N28E077 source pack and real OSM. Further regions require their own verified source coverage. Area clipping does not recompute impact or population metrics; returning to simulation restores the full cached Delhi model/scope.

Selecting or reassessing an area clears hypothetical playback, water and damage. A confirmed current user report enters Response as **reported and unverified**. Only server-owned, verified, dated incident observations within 24 hours can establish verified-current evidence. Historical 2023 data and simulated hazards cannot establish an incident now. Real mapped facilities are filtered to the selected view; text reports do not create flood depths.

Gemini can rank allowed action IDs and cite supplied evidence IDs. Server templates provide wording and measurements come from computed files. Unknown citations, fabricated fields and dispatch commands fail validation. All constraints and missing-data notices remain. Failure returns a labelled deterministic checklist, not an AI success. No teams are contacted or vehicles dispatched. Meko is optional durable build/provenance memory, not operational truth.

## Stopped work and scientific limits

The population download, product refresh, EE readiness gate and replay waiter were stopped by request; completed products and partial source files are preserved. New recordings were deprioritized. Existing intro remains preserved; the attempted earthquake capture failed and no earthquake MP4 is claimed.

The genuine early-July radar before mask, calibration and scored static 2023 replay remain missing. Publication-matched stage anchors exist, but do not supply those missing inputs or current-event evidence. Earth Engine noncommercial eligibility expired; no further setup is running.

Original breach/debris edit cells stayed dry and all saved depths equalled +1 m bitwise. They demonstrated no effect. The newer source-current shoreline cut (`product_3009e5128b1a512a`) now passes the complete file audit. Its stored matched +3 m comparison reports 28 native 10 m cells changing by more than 1 mm, maximum absolute depth difference 0.307300 m and 0.001 km² additional affected land. An independent read of the exported 40 m maximum-depth grids finds three differing cells above 1 mm and maximum difference 0.306368 m. Final building bands equal the unedited +3 m run; this is a small local effect, not a large new flood. The exact label remains **Hypothetical riverbank cut; engineered levee unconfirmed**. No new recording or observed-flood validation is claimed. The refresh batch stopped later.

| Current cached event | Frames / simulated time | Final affected land | Final modelled road closures |
|---|---:|---:|---:|
| River +3 m | 31 / 0–300 s | 1.4322 km² | 155 |
| ITO riverbank cut | 31 / 0–300 s | 1.4332 km² | 155 |
| Earthquake M6 east bank | 4 / 0–60 s | 151.175 km² | 2,138 |
| Old Delhi market fire | 31 / 0–300 s | 0.0034 km² | 15 |
| Earthquake then river +3 m | 33 / 0–360 s | 151.25 km² | 2,293 |

The fire changes from one burning building initially to four burning and eight burnt buildings at the final frame. All five are hypothetical models, not forecasts or observed incidents. Null population totals in partially covered affected areas remain null; known-cell counts are separate. The audit verifies saved-file integrity and progression, not empirical model accuracy. Full details are in the workspace handoff `outputs/cached-events-audit.json`; reproduce with `python3 scripts/check_cached_events.py`.

Fresh Drone proof completed on 77 real CC0 Aukerman Ohio photos and real USGS LiDAR: 8,734,937 dense points, 0.5 m nDSM, and 99,261 actual points in the display preview. Surface NMAD 1.059601 m, RMSE 5.294563 m, bias −0.094513 m, tilt 0.99215°. Scale/rotation are unavailable from only 2 unambiguous held-out patches. Building medians ours/LiDAR are 0.226/3.963 m, 0.189/3.167 m and 0.227/3.320 m. The large mismatch, different source years and weak XY registration are material limitations, not an accuracy success. This is not Texas or Delhi drone evidence.

## Deployment and remaining verification

The local defaults are SQLite metadata and validated local files. A PostgreSQL/Yugabyte metadata adapter and explicit server worker/publication contract are prepared. Managed database, object storage and server resources have not been deployed. A relational-metadata/object-artifact design is described in the current architecture handoff, with official sources and a clear distinction between implementation and proposal.

The chunk-shadow correction passed visual confirmation. Keep unavailable live capacity, road observations, safe-route certification, current occupancy, reported flooded places and freshness data explicit. Do not restart stopped jobs or reclassify old performance/capture measurements as results for the new LOD viewer.

## Historical evidence

Earlier detailed data, solver runtime, validation and recording evidence is preserved in `docs/diorama-check-20261101-0459-IST.md`, `docs/diorama-check-20261101-0550-IST.md` and matching `data/derived/check_history/` snapshots. Their active-job, pending-drone, old Gemini-error and imagery states are superseded. Current source files remain `data/derived/` and `runs/`; current process records remain `logs/jobs.json` and `logs/STATUS.md`.

Historical area-first visual update recorded 01 November 2026, 06:10:45 IST. That QA server/browser were stopped after those checks; current canvas-workspace process status is recorded above.

Final transition QA passed: loading cached +3 m and reopening Select area removed simulated-water labels/playback, disabled the prior impact/response state, restored the selected clip and showed no stale assessment.

At the historical area-first teardown, the QA browser was closed, the foreground server stopped with Ctrl-C, registered active jobs were zero and localhost ports 5173 and 8000 were closed. The user subsequently started the current server. Server deployment README/files are prepared, not executed.

The earlier area-first outcome was saved to Meko otec-build: daf0f498-a352-4b2b-ba0a-9cf2c657161f. The CSS, event-state and rendering-fix summaries were also saved successfully. Only the separate detailed cached-event audit upload was rejected by automatic approval review because it would send internal project/run metadata to external Meko without sufficiently specific destination authorization. That detailed upload was not completed or retried; its full report and current status are saved locally. This does not affect the successfully saved UI summaries, playback or scientific files.
