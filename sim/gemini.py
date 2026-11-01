"""Server-only Gemini text client. No dotenv, credential files, or paid retries."""
import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

import requests

ROOT = Path(__file__).absolute().parents[1]
CONFIG_PATH = ROOT / "data" / "derived" / "gemini_config.json"
SERVICE = "generativelanguage.googleapis.com"
BASE_URL = f"https://{SERVICE}/v1beta"
CHECK_MARKER = "OTEC_GEMINI_OK"
MAX_PROMPT_CHARS = 32768
MAX_OUTPUT_CHARS = 65536
PROJECT_PATTERN = r"(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[1-9][0-9]{5,19})"
RESOURCE_PATTERN = rf"projects/({PROJECT_PATTERN})/locations/global/keys/([a-zA-Z0-9_-]{{1,128}})"


class GeminiError(RuntimeError):
    """Public error text contains neither credentials nor raw provider output."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Config:
    project: str
    configuration: str
    key_resource: str
    model: str


def model_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:models/)?gemini-[a-z0-9][a-z0-9._-]{0,119}", value):
        raise GeminiError("configuration", "Use a valid Gemini model identifier.")
    return value if value.startswith("models/") else "models/" + value


def load_config():
    # Only this fixed nonsecret JSON contract is read. Reject links before opening
    # so configuration cannot indirectly point into a protected credential file.
    if any(path.is_symlink() for path in (CONFIG_PATH, *CONFIG_PATH.parents)):
        raise GeminiError("configuration", "Gemini configuration must not use symlinks.")
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise GeminiError("configuration", "Gemini configuration is too large.")
        data = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        raise GeminiError("configuration", "Valid data/derived/gemini_config.json is required.") from None
    if not isinstance(data, dict) or set(data) != {"project", "configuration", "key_resource", "model"}:
        raise GeminiError("configuration", "Gemini configuration requires exactly project, configuration, key_resource and model; never store a key string.")
    if not isinstance(data["project"], str) or not re.fullmatch(PROJECT_PATTERN, data["project"]):
        raise GeminiError("configuration", "Invalid configured Google project.")
    if not isinstance(data["configuration"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", data["configuration"]):
        raise GeminiError("configuration", "Invalid named gcloud configuration.")
    if not isinstance(data["key_resource"], str) or not re.fullmatch(RESOURCE_PATTERN, data["key_resource"]):
        raise GeminiError("configuration", "Use a fully qualified global API-key resource name.")
    return Config(data["project"], data["configuration"], data["key_resource"], model_name(data["model"]))


def _gcloud_json(config, arguments):
    command = ["gcloud", f"--configuration={config.configuration}", f"--project={config.project}", "--quiet", "--verbosity=none", "--no-log-http", *arguments, "--format=json"]
    environment = os.environ.copy()
    environment.update(CLOUDSDK_CORE_DISABLE_FILE_LOGGING="1", CLOUDSDK_CORE_LOG_HTTP="false", CLOUDSDK_CORE_VERBOSITY="none", CLOUDSDK_CORE_DISABLE_PROMPTS="1")
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=30, env=environment)
    except (OSError, subprocess.SubprocessError):
        raise GeminiError("credentials", "Could not read the configured Gemini credential through gcloud.") from None
    if result.returncode:
        raise GeminiError("credentials", "gcloud could not read the configured project or key. Check the named account's existing access.")
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise GeminiError("credentials", "gcloud returned an invalid credential response.") from None
    if not isinstance(value, dict):
        raise GeminiError("credentials", "gcloud returned an invalid credential response.")
    return value


def _restricted_key(config):
    # Cloud Run injects this value from a single Secret Manager secret. The
    # deployment tool verifies the existing key's ownership/restriction first.
    if os.environ.get('K_SERVICE') and os.environ.get('OTEC_GEMINI_SECRET'):
        key = os.environ['OTEC_GEMINI_SECRET']
        if not re.fullmatch(r'[a-zA-Z0-9_-]{20,256}', key):
            raise GeminiError('credentials', 'Invalid server-managed Gemini credential.')
        return key
    # Fully qualified key names override --project in gcloud: verify ownership.
    project = _gcloud_json(config, ["projects", "describe", config.project])
    number = str(project.get("projectNumber", ""))
    if not re.fullmatch(r"[1-9][0-9]{5,19}", number) or config.project not in (number, project.get("projectId")):
        raise GeminiError("credentials", "Could not verify the configured Google project identity.")
    resource = re.fullmatch(RESOURCE_PATTERN, config.key_resource)
    if resource is None or resource[1] not in (config.project, number):
        raise GeminiError("restrictions", "API-key resource does not belong to the configured project.")
    canonical = f"projects/{number}/locations/global/keys/{resource[2]}"
    description = _gcloud_json(config, ["services", "api-keys", "describe", canonical])
    restrictions = description.get("restrictions")
    targets = restrictions.get("apiTargets") if isinstance(restrictions, dict) else None
    if description.get("name") != canonical or description.get("deleteTime") or not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict) or targets[0].get("service") != SERVICE:
        raise GeminiError("restrictions", "Refusing a key without an exclusive Gemini API service restriction.")
    key = _gcloud_json(config, ["services", "api-keys", "get-key-string", canonical]).get("keyString")
    if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{20,256}", key):
        raise GeminiError("credentials", "gcloud returned an invalid API-key value.")
    return key


def _response_text(payload):
    if not isinstance(payload, dict):
        raise GeminiError("response", "Gemini returned an invalid response.")
    feedback = payload.get("promptFeedback", {})
    if not isinstance(feedback, dict):
        raise GeminiError("response", "Gemini returned invalid prompt feedback.")
    if feedback.get("blockReason") not in (None, "", "BLOCK_REASON_UNSPECIFIED"):
        raise GeminiError("blocked", "Gemini blocked this prompt; no generated answer is available.")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise GeminiError("empty", "Gemini returned no completed text candidate.")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish == "MAX_TOKENS":
        raise GeminiError("truncated", "Gemini reached the output-token limit; partial output was withheld. No automatic retry was made.")
    if finish != "STOP":
        raise GeminiError("blocked" if finish in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY") else "incomplete", "Gemini did not return a completed, usable text answer.")
    ratings = candidate.get("safetyRatings", [])
    if not isinstance(ratings, list) or any(isinstance(item, dict) and item.get("blocked") is True for item in ratings):
        raise GeminiError("blocked", "Gemini blocked this candidate; no answer is available.")
    content = candidate.get("content", {})
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
        raise GeminiError("response", "Gemini returned invalid text parts.")
    visible = [part["text"] for part in parts if part.get("thought") is not True and isinstance(part.get("text"), str)]
    text = "".join(visible).strip()
    if not text:
        raise GeminiError("empty", "Gemini returned no nonempty visible text.")
    if len(text) > MAX_OUTPUT_CHARS:
        raise GeminiError("response", "Gemini returned text beyond the client output bound.")
    return text


def generate_text(prompt, *, max_output_tokens=512, temperature=None, model=None, response_schema=None):
    """Make exactly one paid generation request; return completed visible text."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise GeminiError("input", "Prompt must contain 1–32768 characters of nonempty text.")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4096:
        raise GeminiError("input", "max_output_tokens must be an integer from 1 to 4096.")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise GeminiError("input", "temperature must be a finite number from 0 to 2.")
    config = load_config()
    selected = model_name(model) if model is not None else config.model
    key = _restricted_key(config)
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"candidateCount": 1, "maxOutputTokens": max_output_tokens}}
    if temperature is not None:
        payload["generationConfig"]["temperature"] = temperature
    if response_schema is not None:
        if not isinstance(response_schema, dict): raise GeminiError('input', 'Structured response schema must be an object.')
        payload['generationConfig'].update(responseMimeType='application/json', responseJsonSchema=response_schema)
    try:
        with requests.Session() as session:
            # Avoid implicit netrc credentials/proxies and any POST retries.
            session.trust_env = False
            session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))
            response = session.post(f"{BASE_URL}/{selected}:generateContent", headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=payload, timeout=(10, 90), allow_redirects=False, verify=True)
    except requests.RequestException:
        raise GeminiError("network", "Gemini generation failed at the network layer; the result is unknown and was not retried.") from None
    status = response.status_code
    if status == 402:
        raise GeminiError("credits_depleted", "Gemini credits are depleted or payment is required (HTTP 402). Restore provider credits before trying again; no retry or model fallback was made.")
    if status in (401, 403):
        raise GeminiError("access_denied", "Gemini rejected access (HTTP 401/403). Check the configured key's existing project access.")
    if status == 404:
        raise GeminiError("model_unavailable", "The configured Gemini model is unavailable (HTTP 404); no model fallback was made.")
    if status == 429:
        raise GeminiError("rate_limited", "Gemini quota or rate limit reached (HTTP 429); no automatic retry was made.")
    if status != 200:
        raise GeminiError("upstream", "Gemini returned an unsuccessful response; no automatic retry was made.")
    try:
        data = response.json()
    except (ValueError, requests.RequestException):
        raise GeminiError("response", "Gemini returned invalid JSON.") from None
    text = _response_text(data)
    if key in text:
        raise GeminiError("response", "Gemini returned an invalid credential-reflecting response; output withheld.")
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="Server-side Gemini client; no environment-file loading or automatic paid retries")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Make one real bounded generation and verify its marker; provider charges may apply")
    ask = commands.add_parser("ask", help="Make one real text generation")
    ask.add_argument("text")
    ask.add_argument("--max-output-tokens", type=int, default=512)
    try:
        args = parser.parse_args(argv)
        if args.command == "check":
            result = generate_text(f"Reply with exactly {CHECK_MARKER} and no other text.", max_output_tokens=512)
            if result != CHECK_MARKER:
                raise GeminiError("check_failed", "Generation completed but did not match the requested check marker.")
            print(f"Gemini generation check passed: {CHECK_MARKER}")
        else:
            print(generate_text(args.text, max_output_tokens=args.max_output_tokens))
        return 0
    except GeminiError as exc:
        print(f"Gemini {exc.code}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Gemini request interrupted; its outcome may be unknown. No retry was made.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
