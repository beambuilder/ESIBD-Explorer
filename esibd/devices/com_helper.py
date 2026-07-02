"""Helper to read COM port assignments from the central lab config.

Lookup order:
1. ``[com_ports]`` table in the canonical ``lab_config.toml`` — located via
   the ``LAB_CONFIG`` environment variable (the same file the lab_services
   master and facility services use).
2. Fallback: the legacy ``com_ports.json`` next to this file.
"""

import json
import os
import tomllib
from pathlib import Path

COM_PORTS_FILE = Path(__file__).parent / 'com_ports.json'
LAB_CONFIG_ENV = 'LAB_CONFIG'


def _from_lab_config(device_key: str):
    """Return the port from lab_config.toml's [com_ports], or None."""
    config_path = os.environ.get(LAB_CONFIG_ENV)
    if not config_path:
        return None
    try:
        with open(config_path, 'rb') as f:
            config = tomllib.load(f)
        value = config.get('com_ports', {}).get(device_key)
        return int(value) if value is not None else None
    except (FileNotFoundError, tomllib.TOMLDecodeError, ValueError, TypeError):
        return None


def getComPort(device_key: str, default: int = 1) -> int:
    """Return the COM port number for *device_key*.

    Reads lab_config.toml (via the LAB_CONFIG env var) first, then falls
    back to com_ports.json, then to *default*.
    """
    port = _from_lab_config(device_key)
    if port is not None:
        return port
    try:
        with open(COM_PORTS_FILE) as f:
            ports = json.load(f)
        return int(ports.get(device_key, default))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return default
