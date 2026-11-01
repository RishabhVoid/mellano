# Otec diorama verification — 1 November 2026

The real FABDEM/OSM/Google diorama, five explicitly hypothetical relative-stage runs, source-audited stage table and split-view recording are complete. Full acceptance is **not yet complete**: live satellite imagery, calibrated July 2023 replay, independent drone validation and the full performance target remain pending. Both selected breach/debris edits have **no demonstrated effect** in their 300-second runs. No placeholder observations or validation metrics were created.

This is a canonical-file snapshot at 1 November 2026, 04:59 IST. Background jobs may advance after this document. Earlier verified terrain/building facts below are retained from the 04:10–04:14 IST check; they were not remeasured during this documentation update.

## Final data and counts

Both existing Google downloads finished. No duplicate download was started for this request. The footprint stream scanned 73,380,259 source rows, retaining 486,294 AOI candidates; confidence and overlap filtering then produced these final counts.

| Footprint source | Footprints | With height | Google 2023 median | OSM fallback | Height unknown |
|---|---:|---:|---:|---:|---:|
| OpenStreetMap | 40,195 | 38,548 | 38,353 | 195 | 1,647 |
| Google Open Buildings added | 165,410 | 153,037 | 153,037 | 0 | 12,373 |
| Total | 205,605 | 191,585 | 191,390 | 195 | 14,020 |

The 195 retained OSM heights comprise 2 explicit `height` values and 193 labelled `building:levels × 3 m` estimates. Unknown heights remain null/flat. Counts are feature counts from `data/derived/building_sources.json`; multipart features can produce multiple rendered polygons. Google additions require confidence ≥0.75 and no intersection with OSM buffered by 2 m. Of 221,019 clipped, confidence-qualified Google candidates, 55,609 were rejected for OSM overlap. Google Temporal uses 11,255,225 valid presence-masked pixels, 2 m local sampling and 4 m effective source resolution; footprint medians exclude holes and missing pixels. Source dates differ, so this is not a uniform 2023 city inventory.

The required `ground_10m.tif`, `surface_10m.tif`, `osm_buildings.geojson` and `embankment_mask.tif` exist. The grid is 1,575 rows × 1,203 columns, 10 m, EPSG:32643, northwest origin [714880,3179180]. Real source elevations range from 195.5991058 to 246.8551025 m. `terrain.bin` matches the final ground raster exactly, Float32 little-endian, row-major north to south. Building coordinates use local east/south metres.

FABDEM N28E077 V1-2 was downloaded from Bristol without Earth Engine login, with ZIP CRC and SHA-256 validation. It is bilinearly resampled from 30 m to 10 m. A fresh independent rasterization of all final height-bearing footprints confirms **surface = FABDEM ground + building heights exactly once**. Copernicus remains only in `copernicus_comparison_10m.tif`; Copernicus minus FABDEM has median 2.766 m and p95 6.830 m. Resampling adds no measured elevation detail, and FABDEM is modelled removal of building/forest bias, not a local surveyed DTM. Previous Copernicus-ground calibration must be recomputed.

FABDEM attribution, shown in Layer notes: “FABDEM V1-2 — Neal, J. and Hawker, L. (2023), University of Bristol. CC BY-NC-SA 4.0. Forest and building height biases removed from Copernicus GLO-30; resampled to 10 m for Otec.” Inherited Copernicus credit is also retained. Source SHA-256: `6764c2c6b277a4dfda138286aca880119a755c2a2f00b836c6b91db035775050`.

Mapped context contains 216 water features, 1,391 green features, 22,776 road features and 22,759 rendered non-tunnel road segments. The 595 bridge-tagged segments are not a count of unique bridges. Widths are cartographic, not surveyed. Normal river remains the real OSM outline, labelled “Real: OpenStreetMap river outline”, without claiming an observed flood depth.

## Viewer and performance

Visually checked the complete block, Yamuna, bridges, main roads and dense buildings on both banks. Full-screen matte background, brighter matte buildings, Fit/Top/Oblique/River/ITO presets, 4× default exaggeration, independent building exaggeration, mapped-layer toggles, dark controls, real-data source labels and missing-data states are in place. The default Fit is approximately 35° and uses the area left of the panel.

Earlier final-data measurements on this Darwin arm64 Mac in Chrome for Testing, at exactly 1920×1080, with buildings, mapped context and SSAO enabled (04:10 IST check):

| Measurement | Frames | Mean fps | Median frame | p95 frame |
|---|---:|---:|---:|---:|
| Whole-block orbit, matte | 179 | 60.019 | 16.70 ms | 17.60 ms |
| ITO neighbourhood orbit, matte | 179 | 59.999 | 16.70 ms | 17.60 ms |

A later saved probe at 04:37:25 IST (`otec-browser-check-20261101.json`, 179 frames, 1920×1080, matte ITO neighbourhood) measured **46.9016 fps**, median 16.70 ms and p95 33.40 ms during background work. It reports a 314,747,948-byte application GPU resource estimate at that viewport, with imagery disabled and zero resident imagery textures. This exceeds the 30 fps orbit threshold in that short matte probe, but it does not establish the requested 60 fps still target or live-satellite performance. The earlier ~60 fps measurements are historical, not a description of current loaded performance.

The ITO benchmark orbits at 350 m radius/210 m above its target; it is a neighbourhood performance check, not proof of street-level satellite detail. Steady still fps was not separately profiled. The existing two merged building meshes met the measured target, so an experimental chunk optimization was not applied.

Earlier application GPU resource estimate after viewport restoration: 229,949,048 geometry bytes + 7,578,900 texture bytes + 41,505,750 render-target bytes = 279,033,698 bytes. This is an allocation estimate at that viewport, not hardware GPU-memory telemetry or a peak claim. Live satellite GPU usage remains unverified.

Imagery implementation uses real Mapbox Satellite XYZ tiles, z13–18, adaptive tile chunks clipped to the existing terrain triangles, exact UTM/Web Mercator projection, distance crossfade, cancellation, disposal and a 96-resident-texture bound. For 512² RGBA tiles with mipmaps this bounds tile textures to about 128 MiB, plus geometry/render targets. The API and viewer queue cap upstream requests at six, with provider-specific exponential/Retry-After backoff. The API has a 48 MiB memory cache and 1 GiB provider-partitioned HTTP disk payload cache; ETag/304, expiry and no-store behavior are tested. Metadata/tiles never enter height, mask or metric computations. Attribution follows the providers of the visible tiles; Mapbox tiles carry the official Mapbox logo and Mapbox/OpenStreetMap/Maxar credits. Keyed Esri World Imagery fallback is implemented with its own credits.

Roof colours are sampled within real footprint polygons at z16+, with holes excluded, and blended using a shader uniform. Facade windows are matte procedural detail scaled to actual height. Layer notes state: “Roofs coloured from satellite imagery. Facades are procedural.” No heights are inferred from imagery.

The authorized project-root Mapbox token works for TileJSON, but repeated real tile attempts returned HTTP 429. No `VITE_ARCGIS_KEY` was supplied for the keyed Esri fallback. A public Esri endpoint returned an image during a permission review, but anonymous persistent proxy caching was not enabled because applicable entitlement/use permission was not established. The viewer showed the matte fallback and a clear rate-limit state. Retry-After cooldown is global and bounded to 30–300 seconds; token-free/network failures also fall back. **No real tile image or roof-colour result was obtained; imagery alignment/appearance, loaded LOD performance and the requested street-level acceptance remain unverified.** Isolated geometry/queue tests covered clipping, UV direction, roof sampling, bounded residency, cancellation and retry. Such tests are not real-imagery acceptance evidence.

## Flood scenarios and stage evidence

The July 2023 replay remains the static 8-neighbour connected-stage fill and is the only run eligible for a satellite overlap score. It has not yet been calibrated or computed on the required real masks. Job B is complete: `stage_2023.csv` contains **10 publication-verified anchors**; its audit produces 248 hourly positions, of which 238 are explicitly interpolated. “Verified” means matched to the dated reporting, not independently certified gauge observations. The 13 July peak has a 19:00 anchor and a 20:00 alternative; its one-hour timing uncertainty propagates to affected intervals. Source URLs, the unavailable original ANI URL and its actual verified syndication are preserved in `stage_2023_sources.json`.

The user-authorized relative experiments are separate from July 2023 replay. `sim/relative.py` and `/run` use a FABDEM-derived longitudinal surface proxy within the real mapped OSM main river, then apply hypothetical +1/+2/+3 m forcing. They carry **“Hypothetical, not a forecast”** and no satellite score. A deterministic NumPy virtual-pipe local-inertial shallow-water approximation uses a fixed 0.25 s step, a 60 s boundary ramp, 300 s total simulated time and 31 saved frames at 10 s intervals. Conservative fluxes, positivity limiting and explicit boundary-volume accounting are checked. It omits advective momentum, surveyed bathymetry, drainage, rainfall and calibrated roughness.

Actual Mac runtimes and computed exposure from each `runs/<id>/metrics.json`:

| Relative run | Runtime | Overland area | Buildings exposed | Road ways exposed |
|---|---:|---:|---:|---:|
| River +1 m | 66.748 s | 0.5565 km² | 50 | 28 |
| River +2 m | 67.105 s | 1.0081 km² | 110 | 31 |
| River +3 m | 67.881 s | 1.5176 km² | 172 | 47 |
| ITO breach, common +1 m forcing | 69.077 s | 0.5565 km² | 50 | 28 |
| Ring Road debris, common +1 m forcing | 88.829 s | 0.5565 km² | 50 | 28 |

Overland area is maximum saved-frame depth >0.1 m outside mapped OSM water polygons. Each mapped footprint is counted once when touched/adjacent cells exceed 0.5 m; footprint cells are impermeable, so this is nearby exposure, not indoor flood depth. Road counts use >0.3 m on mapped ways and are not observed closures.

`runs/relative_validation.json` is a full numerical audit, not validation against observed floods. Both edits are bitwise identical to the unedited +1 m run in all 31 saved frames. The six breach cells and 33 debris cells remain exactly dry at all 1,201 checked states, with zero edit-face flux. Their nearest saved baseline water is approximately 1,102.91 m and 316.23 m away, respectively. The audit reconstructed every saved frame exactly. **These selected edits demonstrate no breach/debris effect in the 300-second experiment.** No geometry was moved and no water was invented to produce a more dramatic comparison.

Custom-run admission is serialized across processes/threads, completed output frames are checked before reuse, interrupted jobs do not leave the UI polling forever, and a computation retains its job lock if its supervising worker dies. Stage/S1 updates do not invalidate hypothetical relative-run IDs. The separate calibrated `make whatif` path still requires a real static replay and is not the source of the five completed relative runs.

## Real satellite and cloud gates

Job A cached three real Planetary Computer RTC clips for available July peak/recession acquisitions; the requested early-July before window is absent from that index, although a real 4 July acquisition was confirmed separately. The consistent Earth Engine GRD_FLOAT fallback remains gated on project registration. **No final before/peak/recession/new-flood masks have been fabricated.** Exact paths `s1_water_before.tif`, `s1_water_peak.tif`, `s1_flood_new.tif`, and `s1_water_recession.tif` are watched automatically; recession uses its own acquisition date and is optional for replay readiness.

`google_api_check.json` confirms the expected account in the isolated `otec` gcloud configuration and both Earth Engine and Gemini APIs enabled. API enablement does not complete Earth Engine project registration; the Console still returns a generic registration error under investigation. One low-priority readiness gate waits for registration. The static replay waiter is active again as a single low-priority process (PGID 47607, `nice15`, numerical threads capped at two), waiting on Job A/B and genuine mask outputs. Waiting is not simulation: it starts calibration/replay only after those gates pass. A closest cited pre-flood gauge proxy is explicitly authorized only with its actual acquisition-time gap recorded; it must not be presented as a contemporaneous measurement.

Gemini OAuth model metadata returned HTTP 403 with the exact reason `ACCESS_TOKEN_SCOPE_INSUFFICIENT`; the current gcloud CLI login does not supply the required scope. Read-only listing of the verified project succeeded and found zero existing API key resources; no secret was retrieved and no key-auth model request could run. No new OAuth authorization, ADC, IAM, billing or key changes were made. Gemini is not used to generate heights, masks, flood scores or drone validation metrics.

## Drone proof in progress

The user-approved public-image path supersedes the unavailable Texas-video plan. The source is **77 original CC0 OpenDroneMap Aukerman photographs**, pinned to commit `4e8031630f4193494c79b1c1d3524108826d1ba9`; downloads, original Git blobs/SHA-256 and EXIF GPS/date were checked. The label is “Real drone images (OpenDroneMap aukerman) and real USGS LiDAR. Delhi drone data is not public.” No Texas or Delhi drone evidence is implied.

The fresh run is `run_20261031T230839622664Z` (1 November, 04:38:39 IST). All 77 images are selected, and completed fresh extraction produced **1,024,310 actual SIFT keypoints**. Matching, sparse reconstruction and GPS alignment have completed: **77/77 registered cameras and 48,787 sparse points**. The GPS camera fit RMSE is **1.115099 m**, a fit residual rather than independent LiDAR validation. The first native dense attempt failed because relative image paths were resolved from the wrong working directory; the explicit dense working directory is now fixed and all 21 focused drone tests pass, including the path-resolution regression. Failed preparation was archived; one retry (PGID 53409) regenerates dense inputs within this same fresh run. Undistortion/import are complete and OpenMVS depth-map computation is confirmed active (PID 53425, group 53409, nice15, two workers); the first real `depth0000.dmap` exists. Dense completion and independent metrics are still pending. The interrupted unbounded database was archived, not reused; current extraction/matching/reconstruction uses at most two native workers and `nice15`. This is not a hard OS-wide CPU quota. COLMAP 4.2.1 CPU, native OpenMVS 2.4.0 arm64 and PDAL 2.10.2 are installed and verified.

The full expanded AOI has USGS `OH_Statewide_Phase1_1_2019` EPT coverage and reference metadata. The actual LAZ crop and reference rasters are pending. Three real **OSM** building footprints were frozen into held-out regions before reconstruction; these footprints are not USGS building measurements. Image capture is 29 June 2016; the LiDAR project range is 4 November 2019–2 May 2020, with exact local flight date unconfirmed. EPT Z is already metres, NAVD88/GEOID12B; no second feet conversion is applied.

Dense reconstruction, separate own-ground 0.5 m nDSMs, the EPT crop and held-out comparison are unfinished. The validation fits only XY translation and vertical offset on disjoint controls, then evaluates held-out surface NMAD/tilt, supported scale/rotation diagnostics and paired per-building medians. Source-date mismatch and insufficient support remain explicit; unavailable diagnostics stay null. **No current `runs/drone_proof/metrics.json` exists.** Grid spacing is not an accuracy claim. See `scripts/drone_proof.md` for the complete source/datum/validation contract.

## Checks and recording

The earlier requested sequence completed with exit code 0:

```sh
uv run --frozen python -m sim.context buildings && make export && make check build
```

The final integrated `make check build` at **04:57:47 IST** ran **93 Python tests: 92 passed and one was skipped** because the optional `earthengine-api` dependency is absent from the base environment. Its credential integration has separate dependency-enabled coverage. TypeScript and Vite production build passed; total job time was **6.116 s**. This includes the earlier API/cache and Sentinel/drone additions; the subsequent native dense working-directory correction separately passed all 21 focused drone tests. Verification used a duplicate-safe nohup job at `nice15` with numerical/build concurrency capped at two. No simulation or browser benchmark was started.

Earlier deterministic matte scene seeks 3→8→3 seconds produced byte-identical 1920×1080 PNGs (SHA-256 `466e2e699333b02fd7dadbb7d895c9b1eac588738129018b226d51b9653dd085`). That historical test is separate from capture cadence and asynchronous satellite loading.

`?scene=03_breach&record=1` completed the captioned split-view recording, with hypothetical/real-source labels visible. The source WebM contains **417 frames over 20.580591 s**, averaging **20.2618 captured fps**. The H.264 MP4 preserves 417 frames at 1920×1080 over 20.445 s (20.3962 average fps, 58,326,048 bytes). Requesting 30 fps from `captureStream` did not achieve a real 30 fps capture. `03_breach.mp4` and `03_breach.png` are saved in the task's `outputs/` directory. This clip illustrates the relative +1 m spread; it is not evidence of a breach effect or a calibrated 2023 flood. `02_replay2023` still requires real computed replay.

## What remains

Complete Earth Engine registration and real-mask acquisition, then calibrate FABDEM and compute/score the static replay. Complete the current Aukerman/LiDAR reconstruction and independent comparison before publishing accuracy numbers. Obtain permitted working Mapbox or keyed Esri tiles and verify ITO alignment, roof colours, streaming FPS and GPU usage. Recheck performance under representative load and produce a genuinely smooth recording if real 30 fps delivery is required. Report the selected dry edits honestly; any different geometry or forcing would be a separate hypothetical experiment.

Local viewer/API services are running at `http://127.0.0.1:5173` and `http://127.0.0.1:8000` at this snapshot. Durable job status and logs are in `logs/jobs.json`, `logs/STATUS.md` and the named job logs; inspect them before starting another worker. The `otec-background-jobs` heartbeat is ACTIVE at a five-minute interval, monitoring existing jobs with the same bounded resource policy and no new heavy browser benchmarks. The optional `sim.serve --imagery-token-file <exact-approved-absolute-path>` launcher reads only the user-selected credential file once and keeps supported Mapbox/ArcGIS values in process memory. `make serve` does not read a credential file. Vite keeps `envDir: false`; assistant file access still requires path-specific approval.

## Sources

- [FABDEM V1-2, University of Bristol](https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn) · [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)
- [OpenStreetMap attribution](https://www.openstreetmap.org/copyright)
- [Google Open Buildings v3](https://sites.research.google/gr/open-buildings/) · [Temporal height catalog](https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1)
- [Mapbox Raster Tiles API](https://docs.mapbox.com/api/maps/raster-tiles/) · [Mapbox attribution requirements](https://docs.mapbox.com/help/dive-deeper/attribution/)
- [Local-inertial shallow-water reference](https://doi.org/10.1016/j.jhydrol.2010.03.027)

Final status refresh saved to Meko `otec-build`, receipt `6222c5bd-629b-4f3c-b0dc-2210eb87dcc1`; the latest confirmed native depth-map progress is recorded as a follow-up checkpoint. The earlier verification memory is retained as historical evidence.
