# Gemini server-side text client

Authentication and live inference are working as of 2026-10-31T23:55:02.711801+00:00. After the user completed provider funding, `uv run --frozen --no-env-file python -m sim.gemini check` made one real request to `models/gemini-3.5-flash-lite` and received the exact completed marker `OTEC_GEMINI_OK` (HTTP 200). The earlier HTTP 402 prepaid-credit blocker is resolved and preserved as historical evidence in `data/derived/google_api_check.json`. The Gemini-only restricted key remains server-side and in memory; no environment files were accessed for this check. The previous `gemini-2.5-flash-lite` alternative returned 404 for new users and is not an automatic fallback.

`sim/gemini.py` reads only the fixed, nonsecret `data/derived/gemini_config.json` contract:

```json
{
  "project": "project-ade28d51-55f0-45fb-bd9",
  "configuration": "otec",
  "key_resource": "projects/794626398039/locations/global/keys/otec-gemini-server",
  "model": "models/gemini-3.5-flash-lite"
}
```

This is a resource name, not the API-key secret. Do not put the key string in this file, an environment file, browser code, command-line arguments, exports or logs. Unknown configuration fields and symlinks are rejected. The helper does not discover or read dotenv/environment files and does not write configuration or credentials.

For each explicit call, the helper uses the named gcloud configuration and project to read the project number, describe the exact key and confirm that its sole service target is `generativelanguage.googleapis.com`. It rejects mismatched-project, deleted or unrestricted keys before reading the key string. gcloud stdout/stderr are captured in memory, HTTP/file logging is disabled, and diagnostic output is never echoed. The key stays in memory and goes only in the `x-goog-api-key` HTTPS header to the fixed Gemini host. Redirects, implicit netrc/proxy authentication and automatic HTTP retries are disabled.

After installation, these commands each make **one real generation request**, which may incur provider charges:

```sh
uv run --frozen --no-env-file python -m sim.gemini check
uv run --frozen --no-env-file python -m sim.gemini ask "Explain the diorama controls briefly." --max-output-tokens 512
```

`check` asks the live model for an exact marker and succeeds only when a completed response matches it. Successful model listing alone is not a successful generation check. HTTP 402 becomes a clear `credits_depleted` error; it does not trigger paid retries or a fallback model. Network timeouts also do not retry because a request may already have reached the provider.

Backend use:

```python
from sim.gemini import GeminiError, generate_text

try:
    answer = generate_text("Summarize these supplied notes.", max_output_tokens=512)
except GeminiError as error:
    # Safe public message/code; no provider body or credential-bearing traceback.
    print(error.code, str(error))
```

Prompts must be nonempty text up to 32,768 characters. Output-token requests are bounded to 1–4,096. Temperature is omitted by default for compatibility with current models; an explicit override must be a finite 0–2 value and supported by the chosen model. An explicit model override must remain a valid Gemini model identifier. Only completed `STOP` candidates with nonempty visible text are returned; thought parts are ignored. Blocked, incomplete, malformed and `MAX_TOKENS` responses produce sanitized errors rather than silently returning partial text. Returned text also has a 65,536-character bound. No raw upstream body is printed on failure.

The CLI prints only completed generated text or a sanitized error. `sim/gemini_api.py` exposes `GET /gemini/status` and explicit `POST /product/runs/{run_id}/briefing {frame_index, question?}`. The Response step requests a briefing only when clicked; one request runs at a time, at least 15 seconds apart. It reads bounded published manifest/metrics/response files, rejects unknown runs and symlinks, and returns the source-file URLs alongside generated text. A real end-to-end briefing for the final +3 m frame succeeded on 2026-11-01. Gemini never invents terrain heights, flood masks, observed measurements or scientific validation metrics. Key creation/funding and any future permission changes remain separate authorized operations.

`tests/test_gemini.py` uses only mocked subprocess/HTTP responses and isolated nonsecret JSON. It verifies project/restriction guards, secret-free diagnostics, header-only transport, no retries/fallback, HTTP 402, blocked/truncated/thought-only responses and the real-generation check contract. These tests make no live API requests.

Contract references: [Gemini generateContent](https://ai.google.dev/api/generate-content), [API-key resources and restrictions](https://docs.cloud.google.com/api-keys/docs/reference/rest/v2/projects.locations.keys), and [gcloud get-key-string](https://docs.cloud.google.com/sdk/gcloud/reference/services/api-keys/get-key-string).
