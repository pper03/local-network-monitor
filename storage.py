"""Versioned local storage. A successful save replaces the old file atomically."""
import copy
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_SETTINGS = {"auto_scan": False, "interval_seconds": 60, "ping_timeout_ms": 500,
                    "resolve_hostnames": True, "history_limit": 100, "theme": "Light",
                    "custom_colors": {"background": "#16202E", "surface": "#223044", "accent": "#60A5FA"}}


def default_data_dir():
    override = os.environ.get("LNM_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "LocalNetworkMonitor"
    return Path.home() / ".local/share/LocalNetworkMonitor"


def validate_settings(value):
    value = value if isinstance(value, dict) else {}
    result = copy.deepcopy(DEFAULT_SETTINGS)
    for key in ("auto_scan", "resolve_hostnames"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    for key, lower, upper in (("interval_seconds", 15, 3600), ("ping_timeout_ms", 100, 2000), ("history_limit", 10, 500)):
        if type(value.get(key)) is int:
            result[key] = max(lower, min(upper, value[key]))
    if value.get("theme") in ("Light", "Dark", "Same as device theme", "Custom"):
        result["theme"] = value["theme"]
    custom = value.get("custom_colors", {})
    if isinstance(custom, dict):
        for key in result["custom_colors"]:
            if isinstance(custom.get(key), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", custom[key]):
                result["custom_colors"][key] = custom[key]
    return result


class Store:
    def __init__(self, directory=None, legacy_path=None):
        self.directory = Path(directory) if directory else default_data_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "data.json"
        self.warning = ""
        self.data = {"schema_version": 1, "settings": DEFAULT_SETTINGS.copy(), "devices": {}, "history": []}
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("schema_version") != 1:
                    raise ValueError("Unsupported data format")
                devices, history = value.get("devices"), value.get("history")
                if not isinstance(devices, dict) or not all(isinstance(v, dict) for v in devices.values()):
                    raise ValueError("Invalid device records")
                if not isinstance(history, list) or not all(isinstance(v, dict) and isinstance(v.get("devices"), list) and all(isinstance(d, dict) for d in v["devices"]) for v in history):
                    raise ValueError("Invalid history records")
                self.data = value
                self.data["settings"] = validate_settings(value.get("settings"))
            except (ValueError, UnicodeError) as error:
                backup = self.path.with_name("data-invalid-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
                self.path.rename(backup)
                self.warning = f"The saved data could not be read ({error}). It was preserved as {backup.name}."
        elif legacy_path and Path(legacy_path).exists():
            try:
                old = json.loads(Path(legacy_path).read_text(encoding="utf-8"))
                if not isinstance(old, dict) or not all(isinstance(d, dict) for d in old.values()):
                    raise ValueError("Invalid legacy records")
                for key, device in old.items():
                    self.data["devices"][key] = {**device, "status": "Unknown"}
                self.commit(self.data)
            except (ValueError, UnicodeError, OSError) as error:
                self.warning = f"Could not import devices.json: {error}. The original file was not changed."

    @property
    def settings(self):
        return self.data["settings"]

    def commit(self, value):
        candidate = copy.deepcopy(value)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        self.data = candidate

    def update_settings(self, settings):
        candidate = copy.deepcopy(self.data)
        candidate["settings"] = validate_settings(settings)
        candidate["history"] = candidate["history"][-candidate["settings"]["history_limit"]:]
        self.commit(candidate)

    def record_scan(self, result):
        candidate = copy.deepcopy(self.data)
        saved = candidate["devices"]
        now = result["timestamp"]
        network = result["info"]["network"]
        for device in saved.values():
            device["status"] = "Not detected" if device.get("network", network) == network else "Unknown"
        current = []
        new_count = 0
        for raw in result["devices"]:
            device = dict(raw)
            key = device["mac"] if device["mac"] != "-" else "IP:" + device["ip"]
            provisional = "IP:" + device["ip"]
            if key not in saved and key != provisional and provisional in saved:
                saved[key] = saved.pop(provisional)
            is_new = key not in saved
            if is_new:
                new_count += 1
                saved[key] = {"first_seen": now, "custom_name": ""}
            record = saved[key]
            record.update(device_name=device["device_name"], hostname=device["hostname"],
                          mac=device["mac"], last_ip=device["ip"], last_seen=now,
                          status="Online", network=network)
            device.update(is_new=is_new, id=key, last_seen=now, status="Online",
                          device_name=record.get("custom_name") or device["device_name"])
            current.append(device)
        snapshot = {"timestamp": now, "info": dict(result["info"]), "devices": current,
                    "new_count": new_count, "known_count": len(current) - new_count}
        candidate["history"].append(snapshot)
        candidate["history"] = candidate["history"][-candidate["settings"]["history_limit"]:]
        self.commit(candidate)
        return snapshot

    def rename(self, device_id, name):
        candidate = copy.deepcopy(self.data)
        candidate["devices"][device_id]["custom_name"] = name.strip()[:80]
        self.commit(candidate)

    def clear_history(self):
        candidate = copy.deepcopy(self.data)
        candidate["history"] = []
        self.commit(candidate)
