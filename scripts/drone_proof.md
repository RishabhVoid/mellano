# Fresh public drone-image proof

The source is [OpenDroneMap Aukerman](https://github.com/OpenDroneMap/odm_data_aukerman), an explicitly CC0 collection of 77 original SONY DSC-WX220 photographs. `scripts/drone_proof_acquire.py` pins commit `4e8031630f4193494c79b1c1d3524108826d1ba9`, verifies each original Git blob and SHA256, records actual EXIF GPS/date, and checks the complete camera envelope against the public USGS EPT coverage index. It never downloads an old reconstructed cloud, mesh, or orthomosaic. Aukerman was selected before Toledo/Waterbury because its original-image license is explicit and its GPS extent has public LiDAR coverage.

Display label: **Real drone images (OpenDroneMap aukerman) and real USGS LiDAR. Delhi drone data is not public.** The former unavailable Texas-video plan is superseded by the user's public-image request. No Texas video or Delhi drone imagery is implied.

The original EXIF date is 2016-06-29, local timezone unspecified. The matching project is `OH_Statewide_Phase1_1_2019`, with project acquisition from 2019-11-04 through 2020-05-02; the exact local tile flight date is unconfirmed. These dates differ from the current fresh reconstruction timestamp. Real landscape and seasonal vegetation changes contribute to measured differences.

## Reference LiDAR and datum

The [USGS public EPT endpoint](https://s3-us-west-2.amazonaws.com/usgs-lidar-public/OH_Statewide_Phase1_1_2019/ept.json) requires no account. PDAL reads only the confirmed AOI and saves a hash-verified reference LAZ crop. Reusing the raw reference crop is allowed; reconstructed drone outputs are always new.

[NOAA source metadata](https://www.fisheries.noaa.gov/inport/item/69581/full-list), process step 2, states that EPT XY was reprojected to EPSG:3857 and original US survey foot Z values were converted to metres. EPT Z is therefore already metres, NAVD88/GEOID12B. Do not apply another feet-to-metres conversion. The original native LAS and the EPT have different units. The pipeline reprojects EPT XY into UTM 17N and preserves its documented metre Z.

EXIF altitude's exact geoid is unspecified. GPS camera alignment establishes a first similarity without using LiDAR. A later local vertical offset is empirical datum/bias fitting, not an authoritative geoid conversion.

## Background jobs

Run from the repository root. The duplicate-safe runner records PID, status, IST timestamps and `logs/job_c*.log`; `logs/STATUS.md` gives current status.

```sh
uv run --frozen --no-env-file python -B -m sim.jobs status
uv run --frozen --no-env-file python -B -m sim.jobs start job_c -- uv run --frozen --no-env-file --with Pillow python -u scripts/drone_proof_acquire.py
uv run --frozen --no-env-file python -B -m sim.jobs start job_c_openmvs -- uv run --frozen --no-env-file python -u scripts/drone_proof_native.py
uv run --frozen --no-env-file python -B -m sim.jobs start job_c_run -- uv run --frozen --no-env-file python -u scripts/drone_proof_run.py
uv run --frozen --no-env-file python -B -m sim.drone_proof status
```

`job_c` is original-image acquisition only; its completion does not mean reconstruction is complete. `job_c_run` selects the originals afresh, waits for native tools, then runs CPU COLMAP feature extraction, exhaustive matching and sparse mapping; GPS alignment; native CPU OpenMVS dense reconstruction; PDAL nDSM gridding; independent validation. Original images with finite GPS and successful decode are selected, with measured Laplacian sharpness recorded. Feature counts come only from the actual new COLMAP database.

OpenMVS import and densification use `-w <current-run>/dense` explicitly because scene image paths are relative to that directory. A failed preparation is preserved with `--archive-interrupted-dense` before regenerating it; completed dense products are never adopted from another run.

Native OpenMVS 2.4.0 arm64 is fetched from its official release and verified against the published SHA256. COLMAP/PDAL installation is a separate authorized job; inspect `job_c_tools` and `logs/job_c_install.log` before claiming readiness. Homebrew configuration-file loading must remain disabled unless the user explicitly authorizes exact protected environment-file reads. No configuration file is discovered or read by the proof scripts.

To protect foreground browser use, native extraction, matching, mapping, undistortion, OpenMVS and numerical-library worker counts are capped at two, with `nice15`. Library I/O and coordination can make total process CPU briefly exceed two core-equivalents; this is a worker limit, not an OS-wide CPU quota. Native option help is checked once for installed COLMAP 4.2.1 and cached under `data/raw/drone_proof/tools/colmap-4.2.1/`.

After an explicit user-authorized resource restart during unfinished extraction, `scripts/drone_proof_resume.py --run <exact-current-run-directory> --archive-interrupted-sparse` preserves the partial database and sparse folders under `interrupted_sparse_*`, records the interruption in `proof.json`, and creates a new database. Original image selection, source hashes and frozen validation regions stay in the same current run. The stopped old process group must be terminated before this single duplicate-safe job is launched. Do not SIGCONT an unbounded extractor, reuse its incomplete database, or start a second installer. A normal continuation after a completed stage omits the archive flag.

Every selection creates a timestamped `data/derived/drone_proof/run_*` directory. Existing sparse or dense products are rejected; a failed reconstruction starts a new run. Source/manifest hashes are checked between stages. Original capture dates, download timestamps and actual fresh run dates are stored separately. No reconstructed clouds from other runs are imported.

For an explicit current run, the low-level CLI stages are `images`, `sparse`, `align`, `dense`, `rasterize`, `validate`, with `--run <exact-current-run-directory>` after selection. The default confirmed manifest is `data/raw/drone_proof/inputs.json` generated by the public runner. Legacy video/telemetry manifests remain supported but are not the selected source.

## Independent comparison

Before reconstruction, the runner freezes disjoint fit and held-out polygons. Real OSM buildings are reserved entirely for held-out evaluation, with additional deterministic terrain blocks and separation gaps. Public OSM core map API is a fallback when Overpass is rate-limited. The original footprint features and their source URLs are retained.

Drone DSM and ground derive only from the fresh dense cloud; SMRF classifies its ground. LiDAR ground uses only classified USGS ground returns. Each nDSM is its own DSM minus its own ground. Ground has a 1 m local IDW stage followed by linear interpolation within its own observed ground hull, at most 20 m from observed ground. The number of interpolated cells is reported. Missing observations remain missing and neither source fills the other. The 0.5 m grid spacing is not a claim of 0.5 m accuracy.

Only XY translation and a vertical offset are fitted on ground in the frozen fit regions. Candidate translation support cannot touch held-out regions. Held-out surface NMAD, RMSE, bias and tilt use the full comparable held-out cells without trimming or scale/rotation correction. Separate held-out ground-patch correspondences provide scale/rotation and XY error diagnostics when terrain supports unambiguous matching; flat or ambiguous patches are rejected. Those diagnostics are not surveyed control. Null values and explicit reasons remain when scale/rotation cannot be measured.

Per-building medians compare paired own-ground nDSM cells within real held-out OSM footprints, with paired-cell counts and coverage fraction. Unsupported buildings have null heights. A source-time mismatch, weak translation fit, or insufficient diagnostics remains visible as a limitation, rather than becoming a fabricated result.

Actual validation writes `runs/drone_proof/metrics.json`, the sole UI metrics source. It also records `all_requested_diagnostics_available`; a measured partial comparison must not be described as all requested diagnostics completed. No metrics file is written before real raster comparison succeeds.

## Viewer/API contract

`get_status()` returns source readiness, native tool readiness and completed steps. Current per-run metadata is mirrored to `data/derived/drone_proof/status.json`. Assets are allowlisted relative paths from that run: original selected JPEGs, sparse/dense PLY, Float32 nDSM grids and metadata, and actual validation JSON. NaN in a grid means unobserved. The source label, current run date and acquisition dates remain visible together. A changed manifest cannot expose prior metrics as current.

See [the diorama verification record](../docs/diorama-check.md) for the application's separate checks. Implementation references: [COLMAP](https://colmap.github.io/), [OpenMVS](https://github.com/cdcseacave/openMVS), [USGS public LiDAR registry](https://registry.opendata.aws/usgs-lidar/).
