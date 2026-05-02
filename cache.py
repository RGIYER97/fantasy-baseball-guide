"""Simple disk cache with TTL for slow external API calls."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

_CACHE_DIR = Path.home() / '.cache' / 'fantasy-baseball-helper'
_DEFAULT_TTL = 4 * 3600   # 4 hours
_SCHEDULE_TTL = 2 * 3600  # 2 hours — probables update more frequently


def _cache_path(key: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = key.replace('/', '_').replace(' ', '_').replace('\\', '_')
    return _CACHE_DIR / f'{safe}.json'


def _to_json(obj: Any) -> Any:
    """Recursively make obj JSON-serializable.

    Dicts with tuple keys are encoded as {"__tuple_keyed": [[key_list, value], ...]}.
    Tuples are serialized as lists (consuming code uses destructuring, which works on both).
    """
    if isinstance(obj, dict):
        if obj and any(isinstance(k, tuple) for k in obj):
            return {'__tuple_keyed': [[list(k), _to_json(v)] for k, v in obj.items()]}
        return {k: _to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json(x) for x in obj]
    return obj


def _from_json(obj: Any) -> Any:
    """Reverse _to_json: reconstruct tuple-keyed dicts; leave other lists as-is."""
    if isinstance(obj, dict):
        if '__tuple_keyed' in obj:
            return {tuple(k): _from_json(v) for k, v in obj['__tuple_keyed']}
        return {k: _from_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_json(x) for x in obj]
    return obj


def cache_get(key: str, ttl: int = _DEFAULT_TTL) -> Optional[Any]:
    p = _cache_path(key)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > ttl:
        return None
    try:
        with p.open('r', encoding='utf-8') as f:
            return _from_json(json.load(f))
    except Exception:
        return None


def cache_set(key: str, value: Any) -> None:
    try:
        with _cache_path(key).open('w', encoding='utf-8') as f:
            json.dump(_to_json(value), f)
    except Exception:
        pass
