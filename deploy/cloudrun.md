# Otec Cloud Run deployment

The cloud image packages a verified, immutable demo snapshot. It includes all
published cached disaster runs, actual terrain/buildings, drone proof assets and
Gemini opening briefings for each module. Scientific source timestamps and
limitations are retained. Snapshot mode avoids mistaking migrated filesystem
timestamps for new scientific inputs. New simulations and regional builds still
require an explicit operator worker.

The runtime uses 4 vCPUs, 8 GiB RAM, startup CPU boost, at most two instances,
and scales to zero. The dedicated `otec-runtime` service account can read the
single Gemini runtime secret. The existing restricted key is transferred through
process memory into Secret Manager; no developer credential directory or
environment file is uploaded. Google Cloud handles HTTPS.

To stage a fresh deployment from the project root:

```sh
uv run --frozen --no-env-file python -m scripts.stage_cloudrun /private/tmp/otec-cloudrun-NEW
```

The staging tool copies explicit code paths and published data, rejects links
and environment-like names, includes companion drone grid binaries, and writes
a receipt. `deploy/cloudbuild.yaml` builds the `runtime-cloud` Docker target on
the standard build machine. Use the staged directory and its explicit `.gcloudignore`
when submitting a build; do not submit the unfiltered repository.

Deployment feature boundaries:

- Advisories contain original prose and validated citations. Missing provider
  output yields the same advisory shape with an honest deterministic label.
- All opening/final frames have cached summaries; the latest opening of each
  disaster module has a real Gemini advisory.
- No current incident observation was invented. The production registry remains
  empty until an operator supplies sourced observations.
- Sentinel-1 before/peak masks and calibrated datum are still missing. The stage
  CSV exists. IoU stays unavailable until real overlapping mask data is supplied.
- Registered source packs support expansion through `region_packs.json`; only
  the verified Delhi pack is registered. Mumbai acquisition, OSRM and new
  hydrology experiments were not performed.
- The checkout had no initial Git commit. Changes were left unstaged because
  committing whole existing files would include pre-existing user work.

Cloud Run CPU boost applies during startup; see the
[official CPU documentation](https://docs.cloud.google.com/run/docs/configuring/services/cpu).
Runtime credentials follow the
[Secret Manager integration](https://docs.cloud.google.com/run/docs/configuring/services/secrets).
