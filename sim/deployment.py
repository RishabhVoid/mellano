"""Read the immutable, verified demo snapshot packaged for Cloud Run."""
from functools import lru_cache
import json
import os

from sim.config import DERIVED


@lru_cache(maxsize=1)
def frozen_snapshot():
    if os.environ.get('OTEC_FROZEN_DEMO') != '1': return None
    path = DERIVED / 'deployment_snapshot.json'
    if any(p.is_symlink() for p in (path, *path.parents)): raise ValueError('Invalid deployment snapshot path')
    if path.stat().st_size > 2_000_000: raise ValueError('Deployment snapshot is too large')
    value = json.loads(path.read_text())
    if value.get('schema_version') != 1: raise ValueError('Unsupported deployment snapshot')
    return value
