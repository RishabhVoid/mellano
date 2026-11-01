# Otec diorama verification — current product snapshot

Snapshot: 2026-11-01T00:20:35.917187+00:00. The real city diorama, fresh drone/LiDAR comparison, Gemini text service, response context and WorldPop AOI are available. The current Scenario → Impact → Response workflow is implemented. Refreshed population-bearing product runs, the separate mapped-shoreline cut experiment and remaining product recordings are still being finalized. The July 2023 calibrated replay remains unavailable because genuine early-July radar input is missing. No placeholder observations or validation scores were created.

Satellite textures were removed at the user's request. Matte is the current viewer mode; satellite alignment, roof colour and tile performance are no longer active completion requirements. Earlier evidence is preserved verbatim in `docs/diorama-check-20261101-0459-IST.md` and `data/derived/check_history/viewer_check_20261101_0459_IST.json`.

## Real terrain and building counts

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


## Current viewer, product flow and performance

Full-screen matte terrain, brighter buildings, the complete Yamuna/bridges/roads/green context, Fit/Top/Oblique/River/ITO presets and source labels are in place. The default view fits the whole block beside the panel. The main workflow is Scenario → Impact → Response; advanced controls remain collapsible. Flood, riverbank cut, earthquake, fire and earthquake/flood chain modules compute hypothetical states on real mapped inputs. Their simplified hazard and response assumptions are visible. The July 2023 option is disabled until actual masks and calibration exist.

Root's Chrome check confirmed the default whole-block fit, impact/response rendering, four modelled vehicle labels matching published response JSON and the fixed sticky Run footer. The first refreshed earthquake run is published: total affected population is unavailable because some affected cells lack source coverage; its known-cell subtotal is 4,727,602.75 people at 98.3987% affected-cell coverage. Remaining refreshed products are being computed sequentially with a shared lock; completed native 10 m flood fields are reused when only population/response inputs change. The new shoreline cut is a separate experiment on the real OSM river outline, labelled “Hypothetical riverbank cut; engineered levee unconfirmed”. Its outcome is pending, so no breach benefit is claimed here.

At 05:46:15 IST, the current 1920×1080 ITO orbit probe measured **41.2985 fps** over 179 frames, median **17.0 ms**, p95 **34.2 ms**, during ongoing background jobs. Tracked GPU allocations total **350,882,636 bytes (~335 MiB)**; this is an application allocation estimate, not driver-level or peak measurement. Two automatically repeated near probes measured 50.4225 and 51.8901 fps, both median 16.7 ms and p95 33.3 ms with the same tracked allocations. All three results are retained: the observed range is 41.3–51.9 fps, with 41.3 as the conservative result. These short tests clear the requested30 fps orbit threshold. Still-view 60 fps has not been separately established. Historical matte probes reached 60.019 fps far/59.999 fps near at 04:10 IST and 46.902 fps near at 04:37 IST; those remain dated history, not current performance claims.

## Real response context and population

The fetched OSM context has **616 mapped facilities**: 131 hospitals, 113 clinics, 15 fire stations, 89 police stations and 268 schools. Their operating status, staff, fleet, capacity and disaster-time availability are unknown. Schools are **potential relief sites**, not designated camps. The graph has **99,000 nodes and 204,823 directed edges**, connected only by shared OSM node IDs. It preserves one-way/access/bridge/tunnel/layer data and avoids false planar bridge intersections. Fallback speeds are explicit model assumptions; turn restrictions, live traffic and routes beyond the AOI are unavailable. Response units are modelled fleets, not observed units. Kenney CC0 Car Kit/Watercraft assets have saved licenses and hashes.

Real **WorldPop 2020 India population counts**, CC BY 4.0, are aligned to the exact 10 m terrain grid. The source is 3 arcseconds (nominally 100 m); allocation adds no new demographic detail. Native pixel counts are apportioned by projected pixel/cell intersection area, assuming uniform density within each native pixel. NoData remains unknown, not zero. There are **1,828,329 valid cells**, **66,396 NoData cells**, **96.4957% grid coverage**, and **5,335,852.03 modelled residents in valid cells**. Float32 count-conservation error is **0.0000231 people**. These are modelled 2020 residents, not live occupancy, casualties or observed evacuees.

The entire AOI was losslessly decoded and independently re-read from already-downloaded official TIFF blocks. Its immutable native clip hash is `17e90cccebd9d19c4282b9c731ddd51dce03ca7d552206f557b16ee787ea24e9`. One existing 2 MiB/s-capped full India download continues to finish the full-source SHA-256. AOI raster availability does not depend on that final checksum. The finalizer reuses the aligned raster; it updates acquisition provenance only. Product identities ignore provenance-only timestamps and full-country-hash updates. If an affected region intersects population NoData, total affected population stays null, with a known-cell subtotal and coverage retained.

## Flood experiments and real 2023 evidence

The deterministic dynamic solver is a fixed-step NumPy virtual-pipe local-inertial approximation for what-if experiments. The static eight-neighbour connected-stage fill remains the only 2023 replay/scoring path. Relative runs use a longitudinal FABDEM surface proxy inside the mapped OSM main river, not measured bathymetry or gauge forcing. Labels say **“Hypothetical, not a forecast”**. Dynamics use dt 0.25 s, a 60 s boundary ramp, 300 s simulated duration and 31 saved frames; advective momentum, drainage, rainfall and calibrated roughness are omitted.

Previously completed relative runs remain preserved:  +1 m 66.748 s,  +2 m 67.105 s,  +3 m 67.881 s, original ITO breach 69.077 s and Ring Road debris 88.829 s. Original breach/debris saved depths are bitwise identical to +1 m. The six breach cells and 33 debris cells stayed dry through all 1,201 internal states, with zero edit-face flux. This is a real no-effect result, not a validated breach demonstration. The new shoreline-cut experiment must be judged separately.

The stage table has 10 publication-verified anchors and 248 hourly positions, 238 explicitly interpolated. The 13 July 19:00/20:00 peak timestamp ambiguity is preserved. Three real Planetary Computer RTC clips are cached, but the requested early-July before period is absent from that index. Earth Engine authenticated access reports expired noncommercial eligibility; further setup work was stopped at the user's direction. **Final before/peak/new-flood/recession masks, FABDEM calibration and scored July2023 replay remain unavailable.** No windows were quietly extended and no flood masks invented. Genuine mask files are watched automatically. Any authorized closest-stage proxy must retain its actual acquisition-time gap.

## Fresh drone proof and independent comparison

The authorized public-image source superseded the Texas-video plan: **77 CC0 OpenDroneMap Aukerman photographs from Ohio**, captured 29 June 2016, compared with real USGS `OH_Statewide_Phase1_1_2019` LiDAR (project acquisition 4 November 2019–2 May2020; exact local flight date unconfirmed). The visible label says real Aukerman images/real USGS LiDAR and that Delhi drone data is not public.

Fresh run `run_20261031T230839622664Z` began 04:38:39 IST and completed validation 05:40:02 IST. It regenerated **1,024,310 SIFT keypoints, 48,787 sparse points, 77 registered cameras and 8,734,937 dense points**. A header-driven streaming PLY adapter strips list properties for PDAL without changing scalar values; original dense output is untouched, with source/output checksums. Only rasterization/LiDAR/validation resumed after the adapter correction; sparse/dense outputs were not regenerated again. Separate own-source ground and 0.5 m nDSM rasters, USGS reference rasters and paired comparison are complete.

`runs/drone_proof/metrics.json` reports:

| Independent comparison / fit | Value |
|---|---:|
| Fitted XY translation | +8.2449 m, −5.8544 m |
| Fitted vertical bias/datum offset | +34.8781 m |
| Held-out surface NMAD | 1.059601 m |
| Held-out surface RMSE | 5.294563 m |
| Held-out surface bias | −0.094513 m |
| Held-out surface cells | 62,184 |
| Tilt | −11.4462 / +12.9961 m per km;0.99215° |
| Scale / rotation | Unavailable: only 2 unambiguous held-out patches |

Per-building median ours/LiDAR is 0.226/3.963 m (489 paired cells), 0.189/3.167 m (3 cells, 10% coverage) and 0.227/3.320 m (47 cells, 85.45% coverage). These large differences are reported honestly. Source dates differ by years, the empirical vertical shift is not a known geoid conversion, and weak terrain constraints make XY translation poorly identifiable. Grid spacing is not accuracy. This result does not establish reliable building-height reconstruction or all requested diagnostics.

The Drone tab shows actual stage stills/counts, nDSM, metrics and per-building values. Root visually verified the dense preview and metrics with no browser errors. A deterministic every 88th-point sample has **99,261 actual points**, **2,680,373 bytes**; HTTP 200 and PLY header were verified after API restart. Full cloud/metrics are unchanged. This bounds browser work without creating synthetic points.

## Gemini, checks and recordings

Gemini authentication **and live generation work**. The explicit server helper uses `models/gemini-3.5-flash-lite`; one real request returned HTTP 200 and exact marker `OTEC_GEMINI_OK` at 05:25:02 IST. A real product briefing also succeeded. A Gemini-only API-restricted key is retrieved through the named `otec` gcloud configuration and kept in memory. The browser never receives the key. Earlier OAuth-scope 403/prepaid 402 errors are resolved history; no new secret access was needed for this report. Gemini explains computed files and does not generate terrain, masks, flood scores or scientific metrics.

The requested buildings/export/check/build sequence completed previously. The latest full integrated `make check build` completed **05:42:55 IST: 153 tests, 152 passed, 1 skipped** (optional Earth Engine integration), TypeScript and Vite production build passed, 11.58 s. Later frontend corrections passed TypeScript; the PLY/preview and Drone focused suite passed 26 tests. Focused reruns are not added to the full-suite total.

The final `01_intro.mp4` has **413 actual 1920×1080 frames over13.780198 s**, **29.9705 average captured fps**, with native cadence preserved and no interpolated frames. MP4 duration is13.781 s; the PNG and probe JSON are saved alongside it. Dark caption backplates were added and this take supersedes the earlier410-frame draft, which is preserved separately. Remaining product recordings await finalized population-bearing runs and shoreline-cut audit. The earlier `03_breach.mp4` is historical:417frames at20.26sourcefps, illustrating +1 m spread with no demonstrated original breach effect. It must not be presented as the new shoreline result or calibrated2023 replay.

## Remaining work and honest limits

Finish sequential product population refresh and the new shoreline-cut audit, then package the remaining requested product recordings. Let the single WorldPop source download finish and record its full-file checksum; the real AOI already works. July2023 replay/score remains blocked on genuine radar/calibration, and EE setup is not being pursued further. Drone scale/rotation cannot be reported from insufficient support; building mismatch/temporal gap stay visible. Reported flooded places and freshness observations remain unavailable unless genuine sources arrive. Satellite texture requirements are removed. Local service status and job details are timestamped in `logs/STATUS.md` and `logs/jobs.json`; they are not an uptime promise.

## Provenance

- FABDEM V1-2: https://doi.org/10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn — CC BY-NC-SA 4.0.
- OpenStreetMap contributors: https://www.openstreetmap.org/copyright — ODbL.
- Google Open Buildings: https://sites.research.google/gr/open-buildings/ and https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1.
- WorldPop 2020 India: https://hub.worldpop.org/geodata/summary?id=6527 — DOI 10.5258/SOTON/WP00645,CC BY 4.0; exact source/count method in `docs/response-data.md`.
- Drone source/datum and adapter provenance: `scripts/drone_proof.md`, `docs/drone-ply-adapter.md` and current run manifests/checksums.
- Gemini live/auth contract: `docs/gemini.md`; sanitized result `data/derived/google_api_check.json`.

Current status saved to Meko `otec-build`, receipt `6e9ab95c-13cd-4977-9670-44802863716e`.
