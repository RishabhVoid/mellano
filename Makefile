.DEFAULT_GOAL := help
.PHONY: help setup check build data grid export calibrate replay score whatif serve web record mp4 context buildings product run

UV ?= uv
NPM ?= npm
HOST ?= 127.0.0.1
API_PORT ?= 8000
WEB_PORT ?= 5173

help:
	@printf '%s\n' 'Otec — local application' '  make run     Build once and serve app + API; Ctrl-C stops everything' '  make setup   Install locked Python and web dependencies' '  make check   Verify Python dependencies, API, and TypeScript' '  make build   Build the production viewer' '  make serve   Start the local FastAPI server' '  make web     Start the viewer and open Chrome on macOS' '  make data grid export  Prepare real AOI data and offline web assets' '  make calibrate replay  Requires real masks and cited gauge stages'

setup:
	@command -v $(UV) >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/getting-started/installation/'; exit 1; }
	@command -v $(NPM) >/dev/null || { echo 'Install Node.js 22.12+ (or 24 LTS) first.'; exit 1; }
	$(UV) sync --frozen
	$(NPM) --prefix web ci --no-audit --no-fund

check:
	$(UV) run --frozen --no-env-file python -m sim.check
	$(UV) run --frozen --no-env-file python -m unittest tests.test_pipeline tests.test_dynamic tests.test_imagery tests.test_drone_proof tests.test_stages tests.test_relative tests.test_sentinel tests.test_jobs tests.test_api_runs tests.test_export_masks tests.test_gemini tests.test_gemini_api tests.test_response_context tests.test_product tests.test_response tests.test_ply_scalar tests.test_app tests.test_areas tests.test_deployment tests.test_decision_support tests.test_situation_api tests.test_cached_events
	$(NPM) --prefix web run check
	node --experimental-strip-types --test web/tests/eventState.test.mjs web/tests/productEvents.test.mjs web/tests/responseMotion.test.mjs web/tests/responseVisuals.test.mjs web/tests/vehicleAssets.test.mjs

build:
	$(NPM) --prefix web run build

serve:
	$(UV) run --frozen --no-env-file python -m sim.serve --host $(HOST) --port $(API_PORT)

web:
	$(NPM) --prefix web run dev -- --host $(HOST) --port $(WEB_PORT)

product:
	$(UV) run --frozen --no-env-file python -m sim.jobs start product_precompute -- python -u -m sim.product precompute

context:
	$(UV) run --frozen --no-env-file python -m sim.context osm
	$(UV) run --frozen --no-env-file python -m sim.context buildings
	$(UV) run --frozen --no-env-file python -m sim.cli export

buildings:
	PYTHONPATH=. $(UV) run --frozen --no-env-file python scripts/download_open_buildings.py
	PYTHONPATH=. $(UV) run --frozen --no-env-file python scripts/download_building_heights.py
	$(UV) run --frozen --no-env-file python -m sim.context buildings
	$(UV) run --frozen --no-env-file python -m sim.cli export

data grid export calibrate replay score whatif record mp4:
	$(UV) run --frozen --no-env-file python -m sim.cli $@

run:
	$(UV) run --frozen --no-env-file python -m sim.launch --host $(HOST) --port $(WEB_PORT)
