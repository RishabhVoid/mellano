# Fresh drone cloud compatibility and validation

The fresh OpenDroneMap Aukerman reconstruction completed with 8,734,937 dense points. Its original binary OpenMVS PLY includes variable-length per-vertex visibility lists, which the installed PDAL reader rejects. The rasterization adapter reads the actual PLY header, scalar types, endianness and list counts, preserves every scalar byte and vertex order, and omits only list properties and non-vertex elements. It does not transform coordinates or synthesize points.

Original dense cloud remains `data/derived/drone_proof/run_20261031T230839622664Z/dense/scene_dense.ply`. The derived scalar-only file and its full provenance are adjacent as `scene_dense_scalar.ply` and `scene_dense_scalar.json`.

- Original SHA-256: `92bf8140e8917cff3751d35ee4f721cd7bf6cba2aab0c583e59a78850e415056`
- Scalar-only SHA-256: `f4b13fd707bc8adf800a93003e4412e1feed2494b7eb00846e15d62d941925bb`
- Working buffers: about 3 MiB; list payloads are skipped without allocation.
- Adapter and existing drone tests: 25 passed. Installed PDAL 2.10.2 successfully read an isolated converted fixture.
- Sparse reconstruction, GPS alignment and dense reconstruction were preserved. Only rasterization and the remaining LiDAR/validation stages resumed.

The resumed job completed on 2026-11-01 at 05:40:02 IST in 103.512 seconds. Real results are in `runs/drone_proof/metrics.json`; both independent 0.5 m nDSMs are available in the same fresh run folder.

## What the results support

The imagery is from 2016-06-29; USGS Ohio LiDAR spans 2019-11-04 to 2020-05-02, with the exact local flight date unconfirmed. This is Ohio, not Delhi or Texas. Surface differences include change and vegetation as well as reconstruction error.

Only XY translation and a local empirical vertical offset were fitted from the frozen fit area. Held-out surface NMAD is 1.0596 m and RMSE is 5.2946 m over 62,184 cells. XY shift is [8.24489775856475, -5.854368664674847] m and vertical offset is 34.8781 m. The fit's horizontal translation is poorly identifiable; it must not be presented as a surveyed registration. Scale and rotation are null because only two unambiguous held-out ground patches remain. Tilt is [-11.446208994121312, 12.996063474057758] m/km.

The three mapped-building medians also show a large mismatch: ours/LiDAR are 0.226/3.963 m, 0.189/3.167 m and 0.227/3.320 m. The middle building has only three paired cells and 10% coverage. These are computed outcomes, not evidence of acceptable building-height accuracy. The pipeline ran to completion, but `all_requested_diagnostics_available` is false and no blanket accuracy claim is justified.

## Browser preview

The Drone tab uses a deterministic sample of at most 100,000 actual dense-cloud vertices. It preserves vertex scalar bytes and order, writes the exact retained/full counts and sampling stride, and retains the original full cloud as a separate asset. This sample is display-only; the nDSM and validation use the full-resolution reconstruction. Public status and metrics return the current completed run with no missing-data flags, and real image and height-grid assets are available. Scale/rotation remain explicitly unavailable for the scientific reasons above.
