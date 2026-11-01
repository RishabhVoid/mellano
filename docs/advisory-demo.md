The server-owned `data/derived/current_observations.json` accepts an `areas`
object keyed by selected area ID. Each observation needs `id`, `title`, a public
HTTPS `source_url`, timezone-aware `observed_at`, `verified`, `incident_confirmed`
and `kind` (`observation`, `historical` or `simulation`).

No current observation is seeded without a verifiable source. Do not refresh a
historical timestamp to make it current. An independently verified incident no
older than 24 hours enters Response; a user-confirmed report enters Response
labelled unverified. Both branches are exercised using isolated test fixtures.

Generate offline summaries with:

```sh
uv run --frozen --no-env-file python -m sim.briefing_cache
```

Cached deterministic summaries are labelled as such. They are never presented
as Gemini-generated text. Cache reads check the selected frame and a digest of
the canonical computed evidence, so changed source files invalidate old text.

Add `--live` to generate one real Gemini opening advisory per module using the
configured server identity. This makes five provider calls; other cached frames
remain clearly labelled deterministic summaries. Cached advisories additionally
check a digest of their bounded geospatial evidence.

`POST /api/product/runs/{run_id}/advisory` accepts `frame_index` and returns
Advisory v2: `summary`, `findings`, `watch_items`,
`constraints_acknowledged`, `verification_needed`, evidence and sources. Every
finding cites one to six packet evidence IDs. Unknown IDs, unmatched inline
citations, extra dispatch fields and invalid prose lengths trigger the fallback.
The provider also receives the native JSON schema. Facility access is explicitly
modelled; without a run it remains unknown.

After acquiring genuine mask data, refresh overlap fields without rerunning
physics:

```sh
uv run --frozen --no-env-file python -m sim.cli export-metrics --run-id product_RUNID
```

Scoring requires native depth frames, aligns the SAR mask by nearest-neighbour
resampling, and excludes NoData and nonoverlapping pixels. Empty unions remain
undefined. For hypothetical runs the UI describes geometric overlap, not forecast
validation or independent ground truth.
