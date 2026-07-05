"""Shared telemetry sink for lab device plugins (P6.3 sink retrofit).

Explorer-driven devices (ampr12, dmmr8) write their freshly read values
into the shared ``telemetry.db`` (SQLite WAL, workspace ADR-0002) so the
lab_services monitor can show them on a read-only Electronics tab that
survives Explorer crashes.

Resolution mirrors ``com_helper.py``: the ``LAB_CONFIG`` env var points at
the canonical ``lab_config.toml`` (the lab_services master sets it for all
children); its ``[telemetry] db_path`` names the database, relative paths
resolving against the config file's directory — the same rule
lab_services itself uses, so every writer agrees on one file.

Without ``LAB_CONFIG`` (standalone Explorer run) there is no sink and the
plugins behave exactly as before the retrofit.
"""

import os
import time
import tomllib
from pathlib import Path
from threading import Lock

LAB_CONFIG_ENV = 'LAB_CONFIG'
TELEMETRY_MIN_INTERVAL_S = 5.0
"""Minimum seconds between telemetry writes per channel (decimates fast
poll loops like dmmr8's 200 ms interval; matches the facility services'
housekeeping cadence)."""

_lock = Lock()
_sink = None
_resolved = False  # a failed resolution is cached, not retried every call


def _dbPathFromLabConfig() -> 'Path | None':
    """Return the telemetry.db path from lab_config.toml, or None."""
    config_path = os.environ.get(LAB_CONFIG_ENV)
    if not config_path:
        return None
    try:
        config_file = Path(config_path)
        with config_file.open('rb') as f:
            config = tomllib.load(f)
        db_path = Path(str(config.get('telemetry', {}).get('db_path', 'telemetry.db')))
        if not db_path.is_absolute():
            db_path = config_file.parent / db_path
    except (OSError, tomllib.TOMLDecodeError, ValueError, TypeError):
        return None
    return db_path


def getLabSink():
    """Return the process-wide telemetry sink, or None if unavailable.

    One SQLiteSink instance is shared by all device plugins (it is
    thread-safe). Unavailable means: LAB_CONFIG unset, lab_config.toml
    unreadable, or esibd_bs not installed — all cached after the first
    attempt, all non-fatal for the plugins.
    """
    global _sink, _resolved
    with _lock:
        if _resolved:
            return _sink
        _resolved = True
        db_path = _dbPathFromLabConfig()
        if db_path is None:
            return None
        try:
            from devices.telemetry import SQLiteSink
            _sink = SQLiteSink(db_path)
        except Exception:  # noqa: BLE001  # missing esibd_bs, locked/unwritable db: plugins run without telemetry
            _sink = None
        return _sink


class ChannelThrottle:
    """Per-key minimum-interval gate for telemetry writes."""

    def __init__(self, min_interval_s: float = TELEMETRY_MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._last: dict = {}

    def ready(self, key) -> bool:
        """Return True (and stamp the key) if the key's interval elapsed."""
        now = time.monotonic()
        last = self._last.get(key)
        if last is not None and now - last < self.min_interval_s:
            return False
        self._last[key] = now
        return True
