"""Simple disk cache with TTL for slow external API calls."""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from typing import Any, Optional

_CACHE_DIR = Path.home() / '.cache' / 'fantasy-baseball-helper'
_DEFAULT_TTL = 4 * 3600   # 4 hours
_SCHEDULE_TTL = 2 * 3600  # 2 hours — probables update more frequently


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = key.replace('/', '_').replace(' ', '_').replace('\\', '_')
    return _CACHE_DIR / f'{safe}.pkl'


def cache_get(key: str, ttl: int = _DEFAULT_TTL) -> Optional[Any]:
    p = _cache_path(key)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > ttl:
        return None
    try:
        with p.open('rb') as f:
            return pickle.load(f)
    except Exception:
        return None


def cache_set(key: str, value: Any) -> None:
    try:
        with _cache_path(key).open('wb') as f:
            pickle.dump(value, f)
    except Exception:
        pass
