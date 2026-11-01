# Response context data

Run `uv run --frozen python -m sim.jobs start response_context -- nice -n 15 env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 NUMEXPR_NUM_THREADS=2 uv run --frozen python -u -m sim.response_context run`.
The shared runner uses a duplicate-process lock and records `logs/response_context.log`, `logs/jobs.json`, and `logs/STATUS.md`. Completed jobs are reused; inspect the registry before requesting an explicit refresh.

## Coordinate and publication contract

- Canonical files: `data/derived/response_context.json`, `data/derived/road_graph.json`, `data/derived/population_meta.json`; population raster exists only after a successful acquisition.
- Viewer context: `/data/response_context.json`. `/data/road_graph.json` is also published, but the full directed graph is intended for backend routing, not browser rendering.
- Coordinates are `[east metres, south metres]` relative to the existing terrain's northwest UTM origin, EPSG:32643. No longitude/latitude values are interpreted as local coordinates.
- All counts are derived from files. Missing population remains `available:false`, with null coverage/count/total and an acquisition reason; no replacement grid is written.

## Real mapped facilities

OpenStreetMap amenities `hospital`, `clinic`, `fire_station`, `police`, and `school` are queried inside the AOI. Nodes use their mapped position; area features use a labelled footprint/bounding-box representative location, not a claimed entrance. When Overpass is unavailable, cached mapped area facilities can be published with `facilities_status:partial_cached_ways_only`; that is not a complete inventory.

Each station preserves its OSM type and ID, category, name if supplied, source, and local position. Operation, staffing, emergency fleet, capacity, and flood-time availability are **unknown**. Schools are **potential relief sites**, not designated camps. Nearest road-node attachment is a geometric routing assumption, with its distance recorded; it does not verify entrances, legal access, or walking connections.

Source: [OpenStreetMap contributors, ODbL](https://www.openstreetmap.org/copyright). Snapshot timestamps are stored separately for facilities and roads. OSM source is not a statement that a facility is open or safe during a flood.

## Directed road graph

The existing `data/raw/osm_aoi.json` includes road-way node IDs and full coordinates. The graph connects **shared OSM node IDs only**. Roads crossing geometrically without a shared node remain disconnected, including bridge/tunnel crossings. Real bridge ramps connect through their mapped nodes. Each road segment is clipped to the terrain rectangle; new boundary vertices are unique to that segment, so clipping cannot create false junctions.

Classes: motorway, trunk, primary, secondary, tertiary, unclassified, residential, and their links. Edges retain stable way/segment/direction IDs, local geometry, length, highway class, bridge, tunnel, layer, oneway, access, mapped maxspeed, speed provenance, and modelled travel time. Explicit one-way/reverse-one-way tags are respected, with standard motorway/roundabout defaults. Reversible and alternating roads are excluded because their active direction is unknown. `access=no/private` edges remain identified but have `routing_allowed:false`.

Fallback speeds are modelling assumptions in km/h: motorway 60, trunk 50, primary 40, secondary 35, tertiary 30, unclassified 25, residential 25; links use the smaller of their parent speed and 25. Mapped numeric speeds override those values, and mph is converted. These are not live traffic speeds. No emergency one-way override is assumed. Turn restrictions, conditional access/speeds, live closures, bridge deck elevations, and routes outside the AOI are not inferred. The graph preserves actual disconnected components and reports their sizes rather than inventing connections.

## WorldPop population

Requested source: [WorldPop India 2020, approximately 100 m population counts](https://hub.worldpop.org/geodata/summary?id=6527), DOI `10.5258/SOTON/WP00645`, [CC BY 4.0](https://hub.worldpop.org/data/licence.txt). Native resolution is 3 arcseconds; values are modelled people per native cell, not people per square metre. This is a 2020 estimate, not current occupancy.

Acquisition first attempts bounded HTTP range requests. The reader requires HTTP 206, validates Content-Range, caps total payload at 128 MiB, and rejects an HTTP 200 response before reading its body. Both the original source and the official STAC Global 2 2020 100 m alternative returned HTTP 200 without Content-Range on 2026-10-31 UTC.

The user subsequently authorized a whole-India fallback. `response_population` runs `python -m sim.response_context population` under the shared nohup runner, nice 15, and two-thread caps. It checks for at least 5 GiB free, streams to one fixed `.part` file with 256 KiB chunks and a 2 MiB/s transfer cap, validates the completed TIFF, records its SHA-256 and source metadata, and then clips locally. A partial transfer resumes only when the server genuinely returns HTTP 206 with the requested offset; otherwise it preserves the partial file and reports a block without starting a second full transfer. Population remains unavailable during transfer. On success the job automatically allocates counts into the 10 m grid and updates both canonical and web context. A verified native AOI clip is cached separately for reproducibility.

The complete OSM facility snapshot fetched at `2026-10-31T23:56:56Z` contains 616 AOI features: 131 hospitals, 113 clinics, 15 fire stations, 89 police facilities, and 268 schools. These are mapped features, not observed active emergency resources. The graph contains 99,000 nodes and 204,823 directed edges; the largest weakly connected component has 93,422 nodes.

The allocator projects each native pixel polygon to UTM43N and distributes its population count according to its exact intersection area with the 10 m target cells. It sums contributions, masks cells without complete valid source coverage, and records source-window totals, allocated totals, excluded partial-coverage counts, and float32 conservation error. It does not bilinearly resample or copy a native-cell count into every 10 m cell. Uniform density within a native pixel is an explicit assumption; resampling creates no new demographic information.

`population_10m.tif`, when available, matches the existing ground transform, CRS, dimensions and10 m cells. Unit: people per cell. NoData is -9999, distinct from a valid zero. Population metadata includes source/year/license/native resolution, valid and NoData counts, coverage, method, total, and limitations. Downstream flooded-population values must retain the 2020-modelled-population label and report coverage.

## Verification

`uv run --frozen python -m unittest tests.test_response_context -v` verifies shared-node connectivity, bridge separation, unique boundary clips, one-way direction, explicit speed assumptions, population conservation at full/partial boundaries, NoData propagation, and rejection of full-country responses. Fixtures exist only inside tests; no test fixture is published as real data.

## Verified early AOI publication

At 05:42:45 IST on 2026-11-01, all required native Delhi blocks decoded successfully from the single in-progress official download. The 176 × 155 native window was written losslessly and re-read with identical values and masks. Its immutable verification copy is `data/raw/worldpop_2020_aoi_verified.tif`; `population_meta.json` records that clip's SHA-256. The full-country download and its final checksum continue separately, explicitly marked pending in acquisition metadata. No additional network transfer or substitute dataset was used.

The aligned population grid contains 1,828,329 valid cells and 66,396 NoData cells (96.4957% coverage). The valid cells contain about 5,335,852.03 modelled people in 2020. Float32 count-conservation error is +0.0000231 people relative to geometric allocation. NoData is retained; downstream totals must report coverage and cannot treat missing cells as zero. The actual raster dimensions, affine transform and CRS were checked against the ground grid.
