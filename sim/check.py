"""Smoke-check the actual native stack and the API on the demo machine."""

import importlib
import platform
import sys

from fastapi.testclient import TestClient
from sim.api import app


def main() -> None:
    assert sys.version_info[:2] == (3, 11), "Otec requires Python 3.11"
    for name in ("numpy", "scipy", "rasterio", "geopandas", "shapely", "pyproj", "requests", "fastapi", "uvicorn"):
        module = importlib.import_module(name)
        print(f"OK {name} {module.__version__}")
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200, response.status_code
        assert response.json() == {"status": "ok", "service": "otec", "milestone": "disaster-impact-response"}
    print(f"OK /health on {platform.system()} {platform.machine()}, Python {platform.python_version()}")


if __name__ == "__main__":
    main()
