# Job A: real July 2023 Sentinel-1 masks

Launch the duplicate-protected background worker from the repository root:

```sh
uv run --frozen python -m sim.jobs start job_a -- uv run --frozen python -u -m sim.sentinel run
```

Progress is recorded in `logs/job_a.log`, `logs/jobs.json` and `logs/STATUS.md`.
The worker reads only clipped AOI data from publicly signed Planetary Computer RTC assets. It does not download full multi-gigabyte scenes or store signed URLs.

## Current catalog gap and fallback

The exact before window is July 1–6 inclusive. Planetary Computer's RTC and GRD catalogs currently return no scene for that window, and the July 4 asset paths return 404. NASA ASF independently lists `S1A_IW_GRDH_1SDV_20230704T125520_20230704T125549_049274_05ECD4_B090`. Available PC peak/recession clips are cached for comparison; no missing before water mask or threshold is invented.

Earth Engine authentication and an Earth Engine registered Cloud project are needed to complete the exact dates. Once the user authorizes that login, use:

```sh
uv run --with earthengine-api earthengine authenticate
```

Then, after the first Job A has stopped in `needs_input`, run:

```sh
uv run --frozen python -m sim.jobs start job_a -- uv run --with earthengine-api python -u -m sim.sentinel run --ee-project YOUR_REGISTERED_PROJECT_ID
```

This fallback reads all periods from `COPERNICUS/S1_GRD_FLOAT`, avoiding a mixture of Earth Engine sigma0 and PC RTC gamma0 under one threshold. Signed download URLs and credentials remain in memory. No environment files are discovered or loaded by this worker. The AOI is about 1.9 million float32 cells, within Earth Engine's 32 MB synchronous GeoTIFF request limit.

## Scientific contract

- Exact windows: before July 1–6; peak July 12–18; recession July 20–26, all 2023.
- Prefer the before scene's relative orbit for peak (expected July 4 ascending 27 to July 16 ascending 27). A different recession orbit is recorded as a viewing-geometry limitation.
- Align calibrated linear VV backscatter to the existing EPSG:32643 10 m grid; convert by `10 * log10(VV)`; apply a circular focal median of **30 m radius**. Nonpositive/missing data remains unknown, including windows that overlap invalid input.
- Tune thresholds from −30 to −5 dB in 0.25 dB steps against the existing OSM river polygons, using only the before image. Choose the lowest threshold on an exact IoU tie. Apply that unchanged threshold to peak and recession.
- New flood is `peak water AND NOT before water` only where both dates have valid SAR. Output classes are 0 dry, 1 classified water, 255 nodata.
- Save real acquisitions, source product, viewing geometry, threshold curve, OSM reference, coverage/nodata counts and checksums in `data/derived/s1_meta.json`.
- These are **OSM-tuned SAR classifications, not independent ground truth**. Current OSM geometry is not surveyed July 2023 truth; dark SAR pixels can include shadow/non-water, and urban/vegetated flood water may be missed. July 16 is not the July 13 gauge peak.

References: [PC RTC example](https://github.com/microsoft/PlanetaryComputerExamples/blob/main/datasets/sentinel-1-rtc/sentinel-1-rtc-example.ipynb), [PC GRD example](https://github.com/microsoft/PlanetaryComputerExamples/blob/main/datasets/sentinel-1-grd/sentinel-1-grd-example.ipynb), [NASA ASF catalog](https://docs.asf.alaska.edu/api/keywords/), [Earth Engine preprocessing](https://developers.google.com/earth-engine/guides/sentinel1), [S1 GRD and GRD_FLOAT](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD), [Earth Engine download limits](https://developers.google.com/earth-engine/apidocs/ee-image-getdownloadurl).

## Automatic project gate

After the user has authenticated, start `job_a_wait_project` through the same
nohup runner with `uv run --frozen --with earthengine-api python -u -m sim.sentinel
wait-project --ee-project PROJECT_ID`. The gate refreshes authorized credentials
and performs a read-only Earth Engine request every 60 seconds. It reports API,
registration or permission readiness in `logs/STATUS.md`, never logs upstream
credential-bearing exceptions, and launches `job_a` once through the shared
locked duplicate guard when the project becomes usable. It does not enable APIs,
change IAM, create projects or alter billing. Existing running/completed Job A
workers are reused; masks still require genuine scenes in all exact windows.

### Verified gcloud identity

For the verified isolated CLI configuration, append `--gcloud-configuration otec`
to `wait-project` or `run`; the gate passes it unchanged to acquisition. This path
uses only `gcloud --configuration=otec auth print-access-token`, captures the
output in memory, discards stderr, and supplies a refresh handler to Earth Engine.
It does not use ADC or the older persistent Earth Engine account. The handler and
50-minute refresh expiry survive google-auth credential copies. No token is saved
in project logs, output files or Meko. Run the gate with `nice -n 15`; acquisition
inherits that priority. Pause/resume the entire nohup process group with
`os.killpg`, because the runner and uv process have a separate Python grandchild.

The broker follows the Google-auth [refresh-handler contract](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.credentials.html)
and the official [gcloud access-token command](https://docs.cloud.google.com/sdk/gcloud/reference/auth/print-access-token).
