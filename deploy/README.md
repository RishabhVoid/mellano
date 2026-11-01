# Otec deployment preparation

This is a deployment package, not a deployed service. No container image, remote database, cloud account, server build or live regional acquisition was created during preparation. The local app starts with `make run`; it serves the production viewer and API in one foreground process. Ctrl-C stops it. New local heavy computation and export watchers are disabled.

## Runtime layout

The image contains Python code, locked application dependencies, a built viewer, the Phase 6 scene JSON bundled from `web/scenes`, and the licensed vehicle assets. Large terrain/building/run files are mounted separately. The default image starts only `sim.launch --no-build` on port 5173, serving the viewer, `/api/*`, `/data/*`, and vehicle assets from one origin. Python 3.11 follows `pyproject.toml`; Node 24 satisfies the viewer's declared Node 22.12-or-newer requirement.

Prepare an operator-owned server directory, passed through the **process variable** `OTEC_RUNTIME_DIR`, with these subdirectories:

| Directory | Contents | Access |
|---|---|---|
| `public/` | Existing exported Delhi data; new models under `areas/models/` | Web read-only; worker publishes model bundles |
| `cache/` | SQLite metadata, leases and isolated `work/<job>/<attempt>/` directories | Web and worker writable |
| `derived/` | Existing derived data and nonsecret configuration needed by cached feature checks | Web read-only |
| `runs/` | Existing complete canonical run manifests and metrics | Web read-only |
| `logs/` | Application-owned status/log files | Web writable |
| `sources/` | Verified regional FABDEM source tile | Worker read-only |

The container runs as UID/GID 10001. Give that identity write access to `cache`, `logs` and `public/areas/models`; existing source artifacts need only read access. The image and Compose do not copy data from a developer checkout or create missing host mount directories. Preserve data provenance and licenses when staging existing exports. Do not include credential files in the public tree.

The image build context is allowlisted by `.dockerignore`; it excludes runtime data, local dependencies, credentials and environment-file variants. Vite uses process variables only and does not copy public data into the bundle. Python startup does not load an environment file.

## Build and start on the intended server

These are operator commands to run later, not commands executed during preparation. Set `OTEC_RUNTIME_DIR` to the absolute prepared server directory in the process. Use the explicit null environment-file argument on **every** Compose command; Compose otherwise has automatic file lookup behavior. [Docker interpolation documentation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

```sh
docker compose --env-file /dev/null -f deploy/compose.yaml build web
docker compose --env-file /dev/null -f deploy/compose.yaml up web
```

The browser opens at `http://127.0.0.1:5173`. `GET /api/health` is the health check. The prepared Compose binds only loopback: this application has no user authentication, so an externally accessible deployment needs its operator's authentication/TLS gateway before exposing the API or paid Gemini endpoints.

A separate explicit server worker is opt-in:

```sh
docker compose --env-file /dev/null -f deploy/compose.yaml --profile worker up worker
```

This consumes queued area builds; it does not start replay, Sentinel, photogrammetry or product-precompute jobs. Stop it with Ctrl-C, or explicitly stop both services:

```sh
docker compose --env-file /dev/null -f deploy/compose.yaml --profile worker stop worker web
```

No restart policy resurrects stopped workers. A graceful worker stop terminates its owned builder group and marks the partial job interrupted. A hard kill can leave an expired lease; it is marked interrupted when a worker next claims work and is **not** retried automatically. An operator must verify the previous builder stopped before using `python -m sim.areas requeue <job_id>`.

The equivalent native server worker command, from the application checkout, is:

```sh
python -m sim.areas worker --watch --source-tile /sources/N28E077_FABDEM_V1-2.tif --worker-id regional-1
```

Use `--once` instead of `--watch` for one queued job. The regional child runs an absolute script with a repository bootstrap; it works from its isolated job directory without inheriting `PYTHONPATH`. Child numerical threads are capped at two and priority is nice 15; Compose also limits the worker to two CPUs. API requests only enqueue metadata and never launch a builder.

## Real regional source pack

The default builder covers the N28E077 tile: longitude 77–78° E, latitude 28–29° N, subject to actual raster coverage at the grid boundary. The source file must be the previously verified `N28E077_FABDEM_V1-2.tif` with SHA-256:

```text
6764c2c6b277a4dfda138286aca880119a755c2a2f00b836c6b91db035775050
```

Mount it read-only as `sources/N28E077_FABDEM_V1-2.tif`. There is no automatic FABDEM download. The explicit server worker fetches actual OSM building/water/road/green/embankment geometry through Overpass. Failed acquisition fails the job; it never creates empty replacement data. Only explicit OSM height tags become heights; unmapped heights stay null. Building-level tags do not become invented metre heights. FABDEM ground is resampled to 10m without claiming new native detail or using Copernicus surface heights.

FABDEM V1-2 source and license: [University of Bristol dataset](https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn), CC BY-NC-SA 4.0. OSM provenance is retained with ODbL attribution. Outside this source pack, requests remain server/source-pack required; this is not a global ready-to-build service.

Successful exports contain `area-model.json`, `terrain.json`, Float32 little-endian `terrain.bin`, `buildings.json`, `context.json`, Uint8 `landcover.bin`, and `embankments.json`. Validation checks actual projected coverage, finite terrain, byte sizes, building polygons and provenance before publication. Terrain models do not create disaster, population, response, satellite or incident observations. Their descriptor reports these capabilities as unavailable. Source provenance is distinct from authoritative evidence of a current disaster.

## Area and cache contract

`POST /api/areas/select` accepts either `bbox: [west,south,east,north]` or `center: [lon,lat]` plus `size_km`. Sides must be 0.1–30km and area at most 625km²; UTM latitudes are supported without antimeridian wrapping. A contained Delhi view returns immediately with its original assets and `clip_bounds_local_m`. Its `metrics_scope` remains `full_cached_aoi`; clipping the view does not recompute full-AOI metrics.

Other requests return durable job IDs and `status_url`. Region IDs derive from normalized bounds, queue keys include data-policy/model versions, and published model IDs derive from artifact hashes and model version. Duplicate admissions and worker claims are serialized. Model-version changes invalidate reuse. The metadata endpoints never accept executable commands or arbitrary filesystem paths.

A trusted custom builder can be supplied only by an operator's CLI `worker --once --command <executable> ...`. The adapter appends `--area-config <isolated-json>` and `--output <isolated-directory>`, applies a bounded timeout, and validates the same real-data export contract. The builder does not receive the metadata database DSN. Existing real exports can instead be validated and registered with `python -m sim.areas publish <job_id> --export <exact-export-directory>`.

## SQLite or Yugabyte/PostgreSQL metadata

Default metadata is `cache/metadata.sqlite3`. Web and worker on one host share this local directory. For separate hosts, use the optional PostgreSQL-compatible adapter by supplying the **process variable** `OTEC_DATABASE_URL` to both services. No DSN or credentials are placed in these files. No database is provisioned by this package.

The runtime image includes the optional `psycopg[binary]` 3.3.6 driver in addition to the frozen application dependencies. The application creates its own metadata tables/index; the supplied database identity needs access to that schema. If a configured database cannot connect, the API reports unavailable rather than silently splitting state. Native installations lacking the optional driver explicitly report their SQLite fallback. The Yugabyte/PostgreSQL connection path has not been exercised against a live server. [Psycopg installation](https://www.psycopg.org/psycopg3/docs/basic/install.html), [pinned release](https://pypi.org/project/psycopg/3.3.6/).

SQL stores metadata and leases only. Binary models remain outside SQL. The implemented publisher uses a shared POSIX filesystem and publishes an immutable content-addressed bundle before registering readiness. An object-store deployment must preserve `/data/areas/models/<model_id>/...`, hashes and relative asset resolution, and make the whole verified bundle accessible before publishing its READY descriptor. Direct S3/GCS upload and CDN adapters are not implemented. Do not share SQLite across separate hosts; shared metadata plus shared/published binary storage is the multi-host contract.

## Optional Gemini on a native server or container

The existing client uses a named **gcloud CLI configuration**, not `GEMINI_API_KEY`, ADC fallback or a credential baked into the viewer. Its only application configuration file is the nonsecret `data/derived/gemini_config.json`, with exactly `project`, `configuration`, `key_resource`, and `model`. Use the intended existing project/key/model values; the key resource name is not a key string.

At request time, the client verifies project ownership, describes the key, requires its exclusive service restriction to `generativelanguage.googleapis.com`, retrieves the key string through gcloud into process memory, and performs one bounded generation request. The named gcloud identity must already have permission for those reads. The application does not create keys, alter IAM/billing or retry paid requests. A copied historical check report is not proof that the new server's credentials work.

On a native server, install the [official Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk), then have the operator authenticate the intended named configuration using an approved server identity. Configure its existing access outside the application; do not copy a developer's credentials by default. No Google authentication commands were run during this preparation.

The default container deliberately lacks gcloud. The optional `runtime-gemini` image target installs only the public CLI. To use it, provide an operator-managed gcloud configuration directory through process `OTEC_GCLOUD_CONFIG_DIR`, and the exact nonsecret JSON path through `OTEC_GEMINI_CONFIG_PATH`, both readable by UID 10001. The SDK directory is writable for its normal credential refresh. The optional override mounts them at runtime:

```sh
docker compose --env-file /dev/null -f deploy/compose.yaml -f deploy/compose.gemini.yaml build web
docker compose --env-file /dev/null -f deploy/compose.yaml -f deploy/compose.gemini.yaml up web
```

Build the default image first if the worker will run; the Gemini override builds only the web image. The worker continues using the default image and has no Gemini credential mount. If Gemini is unavailable, the viewer, cached models, area queue and grounded human-review checklist still work. Gemini suggestions remain separated from observed evidence and do not dispatch responders or change simulation data. An operator may explicitly run `python -m sim.gemini check` only when one real provider-charged verification request is intended.

## Verification performed

Area service and regional builder passed 16 small isolated fixture tests, without network or background jobs. The actual cached Delhi selection returned ready in about 45ms initially and 9ms on repeat in a temporary metadata store. Docker/Compose builds, server provisioning, live regional acquisition, a live PostgreSQL/Yugabyte connection and server Gemini authentication were not performed.

Container dependency layout follows the [uv Docker integration guide](https://docs.astral.sh/uv/guides/integration/docker/). The [uv 0.13.6 release](https://github.com/astral-sh/uv/releases/tag/0.13.6) and [Psycopg 3.3.6 release](https://pypi.org/project/psycopg/3.3.6/) were checked against their primary release pages. The included image/tool version pins are preparation inputs, not a claim that a deployment image was built or verified.
