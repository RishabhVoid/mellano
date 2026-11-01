"""Disaster modules expose inputs, apply, step, metrics and labels."""
from .base import LABEL,PALETTE,STATE_CODES
from .flood import Flood,Breach,Chain
from .quake import Quake
from .fire import Fire
PLUGINS={'flood':Flood,'breach':Breach,'quake':Quake,'fire':Fire,'chain':Chain}
