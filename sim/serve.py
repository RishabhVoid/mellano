"""Launch the local API; an optional explicit credential-file read never discovers files.

The caller must have permission to read the exact --imagery-token-file path.
Without this argument no environment file is opened. Secrets stay in child
process memory and never enter command-line arguments, logs or generated files.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys


def imagery_values(lines):
    """Extract only supported imagery credentials, without shell evaluation."""
    found = {}
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "): line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in ("VITE_MAPBOX_TOKEN", "MAPBOX_TOKEN", "VITE_ARCGIS_KEY"):
            continue
        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError("An imagery credential assignment has invalid quoting.") from None
        if len(parts) > 1:
            raise ValueError("An imagery credential assignment must contain one value.")
        if parts and parts[0]: found[name] = parts[0]
    result = {}
    if found.get("VITE_MAPBOX_TOKEN") or found.get("MAPBOX_TOKEN"):
        result["VITE_MAPBOX_TOKEN"] = found.get("VITE_MAPBOX_TOKEN") or found["MAPBOX_TOKEN"]
    if found.get("VITE_ARCGIS_KEY"): result["VITE_ARCGIS_KEY"] = found["VITE_ARCGIS_KEY"]
    return result


def mapbox_values(lines):
    return imagery_values(lines).get("VITE_MAPBOX_TOKEN")


def load_imagery_tokens(path):
    chosen = Path(path)
    if not chosen.is_absolute():
        raise ValueError("Use the exact absolute path approved for this read.")
    if chosen.is_symlink():
        raise ValueError("Provide the explicitly approved regular file, not a symlink.")
    try:
        with chosen.open("r", encoding="utf-8") as stream:
            values = imagery_values(stream)
    except (OSError, UnicodeError):
        raise ValueError("Could not read the explicitly supplied imagery credential file.") from None
    if not values:
        raise ValueError("The explicitly supplied file has no nonempty supported imagery credential.")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description="Otec API; no environment-file discovery or automatic loading")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--imagery-token-file", "--mapbox-token-file", dest="imagery_token_file", metavar="ABSOLUTE_PATH", help="Read only this explicitly authorized file once; extract Mapbox/ArcGIS credentials into child memory")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535: parser.error("Port must be between 1 and 65535")
    child_environment = None
    if args.imagery_token_file:
        try:
            values = load_imagery_tokens(args.imagery_token_file)
        except ValueError as exc:
            parser.exit(2, str(exc)+"\n")
        child_environment = os.environ.copy()
        child_environment.update(values)
    try:
        return subprocess.run([sys.executable, "-m", "uvicorn", "sim.api:app", "--host", args.host, "--port", str(args.port)], env=child_environment, check=False).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__": raise SystemExit(main())
