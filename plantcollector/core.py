"""
Collector core: registry + adapters + store + a few health checks + daily totals.
"""

from __future__ import annotations

import bisect
import json
import re
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .adapters import build_adapter
from .adapters.simulator import SimPlant
from .genshare import GensetShares, seed_window
from .registry import Quality, load_registry
from .store import TelemetryStore


POLL_MIN_S = 600                                    # never read the plant more often than this (10 min)
POLL_OPTIONS_S = (600, 1800, 3600, 7200)            # what the dashboard offers: 10 min, 30 min, 1 h, 2 h
# Settings > Reading the devices: the master's live reading (0 = off: the master is then read at the site read)
LIVE_OPTIONS_S = (0, 5, 10, 30, 60, 120, 300, 600)
# ... and each other device: 0 = with every site read, or less often (never more often than the site read)
DEVICE_OPTIONS_S = (0, 1800, 3600, 7200)
SAME_HOST_PAUSE_S = 0.2                             # between two devices behind the same gateway in a site read

# Grid import, checked against the incomers. The site master's Mains Import P is the sum of what MAIN 1-3
# measure (its history shows P Site = IM1 + IM2 + IM3). Since a configuration change on 3 Nov 2026 it reads 0
# while the three incomers import about 2 MW. When the master reads about nothing while the incomers together
# carry power, the master's reading is not believed: grid import is taken from the incomers and the site load
# (gensets + grid + solar on the master) is corrected by the difference. The incomers are read at the site
# read, so a reading of theirs counts for one read interval and a half, plus 2 min - and not once the master
# has seen the grid go since (its Mains Fail alarm coming on, or its mains-available bit going off).
MAINS_ZERO_KW = 5.0                                 # the master's Mains Import P at or below this ...
INCOMERS_ON_KW = 50.0                               # ... while the incomers together carry at least this
STATE_LOOKBACK_S = 900                              # a state bit that does not change is still stored every 5 min


def mains_suspect(master_kw, incomers_kw) -> bool:
    """True when the master's grid reading is about zero while the incomers carry power."""
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    return num(master_kw) and num(incomers_kw) and abs(master_kw) <= MAINS_ZERO_KW and abs(incomers_kw) >= INCOMERS_ON_KW


def carry_forward(rows: list[tuple[float, float]], at: list[float], hold_s: float) -> list[tuple[float, float] | None]:
    """For each time in `at` (ascending), the last of `rows` ((epoch s, value), ascending) at or before it,
    if no older than `hold_s`; else None."""
    out: list[tuple[float, float] | None] = []
    j, last = 0, None
    for t in at:
        while j < len(rows) and rows[j][0] <= t:
            last = rows[j]
            j += 1
        out.append(last if last is not None and t - last[0] <= hold_s else None)
    return out


class Collector:
    def __init__(self, config_path: str, data_dir: str = "./data", *, seed_hours: float = 0.0, fresh: bool = False,
                 initial_poll: bool = True, read_kept: bool = True):
        self.config_path = config_path
        # False in the one-off tools (check, export, report): the meters on a kept connection are left to the running
        # service, whose connection a second one could disturb
        self.read_kept = read_kept
        self.registry = load_registry(config_path)
        self.simulated = all(d.adapter == "simulator" for d in self.registry.devices.values())
        self.plant: SimPlant | None = None
        if self.simulated:
            if (self.registry.extra.get("simulation") or {}).get("model") == "real_plant":
                from .sim_realplant import RealPlantSim            # the real plant's devices (config/site.demo.yaml)
                self.plant = RealPlantSim(self.registry.devices)
            else:
                prof = self.registry.extra.get("site_load", {}).get("profile_kw") or [300] * 24
                self.plant = SimPlant(self.registry.devices, prof)
            if seed_hours > 0:                       # start the demo clock in the past, then catch up
                self.plant.now -= timedelta(hours=seed_hours)
        os.makedirs(data_dir, exist_ok=True)
        db = os.path.join(data_dir, "demo.sqlite" if self.simulated else "telemetry.sqlite")
        if fresh and self.simulated and os.path.exists(db):      # demo data is regenerated; real data is never deleted
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(db + ext)
                except FileNotFoundError:
                    pass
        self.db_path = db
        self.data_dir = data_dir
        self.store = TelemetryStore(db)
        # polling rate: the config value, overridden by what was saved from the dashboard (real plant only)
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.settings_source = "config"
        if not self.simulated:
            saved = self._load_settings()
            if saved.get("poll_interval_s") in POLL_OPTIONS_S:
                self.registry.site.poll_interval_s = float(saved["poll_interval_s"])
                self.settings_source = "settings.json"
        # analyzers added from the dashboard (Analyzers tab > Add analyzer): same meter type, by gateway IP and address
        self.user_added: set[str] = set()
        self.removed: set[str] = set()           # removed from the dashboard while running: gone at the next start
        self._devices_lock = threading.Lock()
        if not self.simulated:
            self._load_added_analyzers()
        # the PC's own plant address, when it has a second network in the same range (config network.source_address)
        from .adapters import modbus_tcp
        modbus_tcp.set_source_address((self.registry.extra.get("network") or {}).get("source_address"))
        self.adapters = {d.id: build_adapter(d, plant=self.plant) for d in self.registry.devices.values()}
        # adapters connect on their first read (a connect to an unreachable device blocks for its timeout)
        self._comms_lost: set[str] = set()
        self._alarm_on: set[str] = set()
        self._grid_off: set[str] = set()
        self._poll_lock = threading.RLock()
        self.after_poll: list = []               # callbacks run after every site read (the Modbus publisher refreshes here)
        self.started_at = datetime.now(timezone.utc)
        self.polls = 0
        self.last_poll_at: datetime | None = None
        # what the Turkey feed shows: the values as they were at the end of the last site read. The live reader
        # updates the master's values every few seconds for the dashboard; Turkey sees them change only here.
        self.site_snapshot: dict[str, dict] | None = None
        self.read_times: dict[str, float] = {}   # device -> epoch of this program's last read of it (the live guard)
        self._last_read_mono: dict[str, float] = {}
        # devices read less often than the site read: config `slow: {device: seconds}`, plus the ones the live
        # guard slowed down (remembered in settings.json)
        self.every_override: dict[str, int] = {}
        slow_cfg = self.registry.extra.get("slow") if not self.simulated else None
        for did, s in (slow_cfg.items() if isinstance(slow_cfg, dict) else []):
            try:
                s = int(s)
            except (TypeError, ValueError):
                continue
            if did in self.registry.devices and s > 0:
                self.every_override[did] = s
        if not self.simulated:
            slowed = self._load_settings().get("slowed_devices")
            for did, s in (slowed.items() if isinstance(slowed, dict) else []):
                try:
                    s = int(s)
                except (TypeError, ValueError):      # a hand edit gone wrong must never stop the collector
                    continue
                if did in self.registry.devices and s > 0:
                    self.every_override[did] = max(s, self.every_override.get(did, 0))
        # what was chosen in Settings > Reading the devices (0 = with every site read); a later slow-down by the
        # live guard removes the choice for that device again
        self.device_choice: dict[str, int] = {}
        if not self.simulated:
            chosen = self._load_settings().get("device_every_s")
            for did, s in (chosen.items() if isinstance(chosen, dict) else []):
                try:
                    s = int(s)
                except (TypeError, ValueError):
                    continue
                if did in self.registry.devices and s in DEVICE_OPTIONS_S:
                    self.device_choice[did] = s
                    if s:
                        self.every_override[did] = s
                    else:
                        self.every_override.pop(did, None)
        self.live = None                         # the live reader (plantcollector.live), started by `run`
        self.meters = None                       # the meter reader (plantcollector.meters), started by `run`
        from collections import deque
        self._sim_history: deque = deque(maxlen=200)       # the demo's live chart (no live reader there)
        # each genset's share of the gensets' power, worked out from the site master (it has no kW per genset)
        self.genshares = GensetShares.for_registry(self.registry)
        self.register_names: dict[str, dict[str, str]] = self._load_register_names()   # the plant's names for registers
        self._share_lock = threading.Lock()
        if self.simulated and seed_hours > 0:
            self.poll()
            self._seed_demo(seed_hours)
        elif initial_poll:
            self.poll()                      # `run` skips this and polls from its own thread, so the
                                             # dashboard is up at once even when devices time out

    def _seed_demo(self, hours: float) -> None:
        """Generate demo history with a couple of scripted events so every panel has content."""
        assert self.plant is not None
        gens = [d.id for d in self.registry.by_kind("genset")]
        script = []                                   # (hours before now, scenario, device)
        if len(gens) > 1 and hours >= 12:
            script += [(min(hours - 1, 30), "comms_loss", gens[1]), (min(hours - 1, 30) - 0.4, "comms_restore", gens[1])]
        if gens and hours >= 6:
            script += [(min(hours - 0.5, 9), "genset_alarm", gens[0]), (min(hours - 0.5, 9) - 1.5, "genset_alarm_clear", gens[0])]
        script.sort(key=lambda s: -s[0])
        left = hours
        for at, scenario, dev in script:
            if left > at:
                self.advance((left - at) * 3600, step=300)
                left = at
            self.plant.inject(scenario, dev)
        if left > 0:
            self.advance(left * 3600, step=300)

    def now(self) -> datetime:
        return self.plant.now if self.plant is not None else datetime.now(timezone.utc)

    # --- settings ---------------------------------------------------------------------

    @property
    def poll_interval_s(self) -> float:
        return float(self.registry.site.poll_interval_s)

    def _load_settings(self) -> dict:
        try:
            with open(self.settings_path, encoding="utf-8-sig") as f:        # a BOM (an edit on Windows) is fine too
                data = json.load(f) or {}
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    _settings_lock = threading.Lock()

    def _update_settings(self, change) -> None:
        """Read settings.json, apply `change(data)`, write it back - one writer at a time (the dashboard's
        API threads and the live reader both write it), through a temporary file of its own."""
        with Collector._settings_lock:
            data = self._load_settings()
            change(data)
            tmp = f"{self.settings_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.settings_path)

    # --- analyzers added from the dashboard: a meter of the same type as the plant's two (the PM800 profile),
    # by its gateway's IP address and its Modbus address. Read-only, by the meter reader on the gateway's kept
    # connection (shared with any meter already read behind that gateway). Kept in settings.json (added_analyzers).
    ADD_PROFILE = "pm800_meter.yaml"
    ADD_MAX = 16

    @staticmethod
    def _analyzer_entry(did: str, host: str, port: int, unit_id: int, name: str = "") -> dict:
        return {"id": did, "name": name, "profile": Collector.ADD_PROFILE, "adapter": "modbus_tcp",
                "connection": {"host": host, "port": port, "unit_id": unit_id, "timeout": 5, "max_block": 16, "keep_open": True}}

    def _check_analyzer(self, host, port, unit_id, name, skip: str | None = None) -> tuple[str, int, int, str]:
        import ipaddress
        try:
            ip = ipaddress.IPv4Address(str(host or "").strip())
        except ValueError:
            raise ValueError("the gateway's IP address must look like 192.168.141.3")
        if ip.is_unspecified or ip.is_multicast or str(ip) == "255.255.255.255":
            raise ValueError(f"{ip} is not a device's address")
        try:
            port, unit_id = int(port), int(unit_id)
        except (TypeError, ValueError):
            raise ValueError("the port and the Modbus address must be numbers")
        if not 1 <= port <= 65535:
            raise ValueError("the port must be 1 to 65535 (Modbus TCP: 502)")
        if not 1 <= unit_id <= 247:
            raise ValueError("the Modbus address (unit ID) must be 1 to 247")
        name = self._clean_name(name)
        # never a plant controller or the SAU: only meter gateways (another device's address is refused)
        others = {str((d.connection or {}).get("host")): d.id for d in self.registry.devices.values() if d.kind != "analyzer"}
        others.update({str(h): "a genset controller" for h in ((self.registry.extra.get("probe") or {}).get("hosts") or [])})
        if str(ip) in others:
            who = others[str(ip)]
            raise ValueError(f"{ip} is {who if ' ' in who else 'the address of ' + who}: only the gateway of a power meter can be added here")
        for d in self.registry.devices.values():
            c = d.connection or {}
            if d.id != skip and d.id not in self.removed and str(c.get("host")) == str(ip) and int(c.get("port", 502)) == port \
                    and int(c.get("unit_id", 1)) == unit_id:
                raise ValueError(f"{d.id} already reads {ip} port {port} address {unit_id}")
        return str(ip), port, unit_id, name

    def _clean_name(self, name) -> str:
        if name is None:
            return ""
        if not isinstance(name, str):
            raise ValueError("the name must be text")
        name = " ".join(name.split())
        if len(name) > self.NAME_MAX:
            raise ValueError(f"the name can be at most {self.NAME_MAX} characters")
        if any(ord(ch) < 32 or ch in "<>" for ch in name):
            raise ValueError("the name cannot contain < or >")
        return name

    def _load_added_analyzers(self) -> None:
        from .registry import build_device
        for e in self._load_settings().get("added_analyzers") or []:
            try:
                did = str(e["id"])
                if not did.startswith("analyzer-") or did in self.registry.devices:
                    continue
                host, port, unit, name = self._check_analyzer(e.get("host"), e.get("port", 502), e.get("unit_id"), e.get("name", ""))
                self.registry.devices[did] = build_device(self._analyzer_entry(did, host, port, unit, name))
                self.user_added.add(did)
            except Exception as exc:              # a hand edit gone wrong must never stop the collector
                print(f"[collector] an added analyzer in settings.json is not valid ({exc}); left out")

    def add_analyzer(self, host, port=502, unit_id=1, name="") -> dict:
        """Add a meter of the plant's analyzers' type, read from now on. Returns its id and connection."""
        if self.simulated:
            raise ValueError("the demo has its own two analyzers; analyzers are added on the plant PC")
        from .registry import build_device
        with self._devices_lock:
            host, port, unit_id, name = self._check_analyzer(host, port, unit_id, name)
            if len([d for d in self.user_added if d not in self.removed]) >= self.ADD_MAX:
                raise ValueError(f"at most {self.ADD_MAX} analyzers can be added here")
            data = self._load_settings()
            used = [int(d.split("-")[1]) for d in self.registry.devices if re.match(r"^analyzer-\d+$", d)]
            n = max(used + [int(data.get("analyzer_last") or 0), 2]) + 1        # never an id used before
            did = f"analyzer-{n}"
            dev = build_device(self._analyzer_entry(did, host, port, unit_id, name))
            ad = build_adapter(dev, plant=self.plant)

            def change(data):
                lst = [x for x in (data.get("added_analyzers") or []) if isinstance(x, dict)]
                lst.append({"id": did, "host": host, "port": port, "unit_id": unit_id, "name": name})
                data["added_analyzers"] = lst
                data["analyzer_last"] = n
            self._update_settings(change)
            # replaced, never changed in place: the site read and the meter reader may be going through them
            self.registry.devices = {**self.registry.devices, did: dev}
            self.adapters = {**self.adapters, did: ad}
            self.user_added.add(did)
        if self.meters is not None:
            self.meters.add_device(did)
        elif getattr(self, "_meters_wanted", False):
            self.start_meters()
        self.store.event("settings", f"{did} added: {host} port {port}, Modbus address {unit_id}" + (f" ({name})" if name else ""),
                         severity="info", code="SETTINGS", device_id=did)
        return {"id": did, "host": host, "port": port, "unit_id": unit_id, "name": name}

    def remove_analyzer(self, did: str) -> None:
        """Stop reading an analyzer added from the dashboard. Its readings stay in the database; it is gone from the
        dashboard now and from the program at its next start."""
        did = str(did)
        if did not in self.user_added or did in self.removed:
            raise ValueError(f"{did} was not added from the dashboard" if did not in self.user_added else f"{did} is removed already")
        with self._devices_lock:
            def change(data):
                data["added_analyzers"] = [x for x in (data.get("added_analyzers") or []) if isinstance(x, dict) and x.get("id") != did]
            self._update_settings(change)
            self.removed.add(did)
            self._comms_lost.discard(did)
            self._alarm_on.discard(did)
        last = self.meters.remove_device(did) if self.meters is not None else True
        if last:                                       # no other meter behind that gateway: free its connection
            try:
                self.adapters[did].close()
            except Exception:
                pass
        dev = self.registry.devices[did]
        self.store.event("settings", f"{did} removed ({dev.connection.get('host')} address {dev.connection.get('unit_id')}); "
                         "its readings stay in the database", severity="info", code="SETTINGS", device_id=did)

    # --- the plant's own names for the analyzers' registers (Analyzers tab, Detailed view) ----------------------
    NAME_MAX = 40
    NAMED_KINDS = ("analyzer",)

    def _named_point(self, did, pid):
        d = self.registry.devices.get(str(did))
        if d is None:
            raise ValueError(f"unknown device {did}")
        if d.kind not in self.NAMED_KINDS:
            raise ValueError(f"the registers of {did} cannot be named here (only those of the {', '.join(self.NAMED_KINDS)}s)")
        p = d.point(str(pid))
        if p is None or "address" not in p.raw:
            raise ValueError(f"{did} has no register {pid}")
        return d, p

    def _load_register_names(self) -> dict[str, dict[str, str]]:
        raw = self._load_settings().get("register_names") or {}
        out: dict[str, dict[str, str]] = {}
        if isinstance(raw, dict):
            for did, names in raw.items():
                if not isinstance(names, dict):
                    continue
                for pid, nm in names.items():
                    try:
                        d, p = self._named_point(did, pid)
                    except ValueError:
                        continue
                    if isinstance(nm, str) and nm.strip():
                        out.setdefault(d.id, {})[p.id] = " ".join(nm.split())[:self.NAME_MAX]
        return out

    def register_list(self, did: str) -> list[dict]:
        """The registers read from an analyzer: address as sent (0-based) and as its manual counts (from 1), how many
        registers the value takes, its kind and unit, the profile's description and the plant's own name ("" = none)."""
        from .adapters.modbus_tcp import _size
        d = self.registry.devices[did]
        own = self.register_names.get(did, {})
        out = []
        for p in d.points:
            if "address" not in p.raw:
                continue
            a, kind = int(p.raw["address"]), str(p.raw.get("kind", "uint16"))
            out.append({"id": p.id, "address": a, "manual": a + 1, "words": _size(kind), "kind": kind,
                        "scale": p.raw.get("scale"), "unit": p.unit, "description": p.description or p.id, "name": own.get(p.id, "")})
        return sorted(out, key=lambda r: (r["address"], r["id"]))

    def set_register_name(self, did: str, pid: str, name) -> str:
        """The plant's own name for one register of an analyzer (e.g. what its meter feeds), or "" for none. Only the
        name shown changes: what is read, the readings, the database and the Turkey feed stay as they are."""
        d, p = self._named_point(did, pid)
        if name is None:
            name = ""
        if not isinstance(name, str):
            raise ValueError("the name must be text")
        name = " ".join(name.split())
        if len(name) > self.NAME_MAX:
            raise ValueError(f"the name can be at most {self.NAME_MAX} characters")
        if any(ord(ch) < 32 or ch in "<>" for ch in name):
            raise ValueError("the name cannot contain < or >")
        if name == self.register_names.get(d.id, {}).get(p.id, ""):
            return name

        def change(data):
            allv = data.get("register_names") if isinstance(data.get("register_names"), dict) else {}
            mine = allv.get(d.id) if isinstance(allv.get(d.id), dict) else {}
            if name:
                mine[p.id] = name
            else:
                mine.pop(p.id, None)
            if mine:
                allv[d.id] = mine
            else:
                allv.pop(d.id, None)
            data["register_names"] = allv
        self._update_settings(change)
        if name:
            self.register_names.setdefault(d.id, {})[p.id] = name
        else:
            self.register_names.get(d.id, {}).pop(p.id, None)
            if not self.register_names.get(d.id):
                self.register_names.pop(d.id, None)
        reg = f"register {p.raw['address']} ({p.id})"
        self.store.event("settings", f"{d.id} {reg} named '{name}'" if name else f"{d.id} {reg}: its own name removed",
                         severity="info", code="SETTINGS", device_id=d.id)
        return name

    def set_poll_interval(self, seconds: int) -> None:
        """Change the polling rate at runtime and remember it. Only the fixed options are allowed;
        never below POLL_MIN_S, to keep the load on the SAU gateway small."""
        seconds = int(seconds)
        if seconds not in POLL_OPTIONS_S:
            raise ValueError(f"poll interval must be one of {sorted(POLL_OPTIONS_S)} seconds")
        if self.simulated:
            raise ValueError("the demo refreshes every 30 s; this setting applies to the real plant config")
        self.registry.site.poll_interval_s = float(seconds)

        def change(data):
            data["poll_interval_s"] = seconds
            data["changed_at"] = datetime.now(timezone.utc).isoformat()
        self._update_settings(change)
        self.settings_source = "settings.json"
        self.store.event("settings", f"poll interval set to {seconds} s", severity="info", code="SETTINGS")

    def close(self) -> None:
        if self.live is not None:
            self.live.close()
        if self.meters is not None:
            self.meters.close()
        if getattr(self, "_archive_stop", None) is not None:
            self._archive_stop.set()
        for a in self.adapters.values():
            try:
                a.close()
            except Exception:
                pass

    # --- read rates -------------------------------------------------------------------

    def device_every_s(self, did: str) -> float:
        """How often this program reads the device: the live interval, a slower one, or the site read's."""
        lv = self.live
        if lv is not None and did in lv.device_ids:
            return lv.every_s
        mr = self.meters
        if mr is not None and did in mr.device_ids:
            return mr.every_s
        return float(max(self.every_override.get(did, 0), self.poll_interval_s))

    def slow_down(self, did: str, seconds: int, why: str) -> None:
        """Read `did` only every `seconds` from now on, and remember it (the live guard calls this)."""
        self.every_override[did] = int(seconds)
        self.device_choice.pop(did, None)
        if not self.simulated:
            def change(data):
                if not isinstance(data.get("slowed_devices"), dict):
                    data["slowed_devices"] = {}
                data["slowed_devices"][did] = int(seconds)
                data["slowed_at"] = datetime.now(timezone.utc).isoformat()
                if isinstance(data.get("device_every_s"), dict):
                    data["device_every_s"].pop(did, None)
            try:
                self._update_settings(change)
            except OSError as exc:
                print(f"[collector] could not save the slower rate of {did}: {exc}")
        self.store.event("read_rate", why, device_id=did, severity="warning", code="READ_SLOWED")

    def live_cfg(self) -> dict:
        """The config's live block with the rate chosen in Settings (live_every_s in settings.json) applied."""
        cfg = dict(self.registry.extra.get("live") or {})
        if not self.simulated:
            saved = self._load_settings().get("live_every_s")
            if isinstance(saved, (int, float)) and not isinstance(saved, bool) and int(saved) in LIVE_OPTIONS_S:
                cfg["enabled"] = int(saved) > 0
                if int(saved) > 0:
                    cfg["every_s"] = int(saved)
        return cfg

    def start_archiver(self, first_after_s: float = 120.0, every_s: float = 3600.0) -> bool:
        """Once an hour (the first time two minutes after the start): every finished month's readings into a file of
        its own (TelemetryStore.archive_months). Real plant only; the demo's data is made again at each start."""
        if self.simulated or not self.store.archive_dir or getattr(self, "_archive_stop", None) is not None:
            return False
        stop = self._archive_stop = threading.Event()

        def run():
            if stop.wait(first_after_s):
                return
            while True:
                try:
                    moved = self.store.archive_months()
                    if moved:
                        print(f"[collector] the readings of {', '.join(moved)} moved to their own files in {self.store.archive_dir}")
                except Exception as exc:                  # never let it stop the collector; it tries again in an hour
                    print(f"[collector] moving a finished month to its own file failed: {exc}")
                if stop.wait(every_s):
                    return
        threading.Thread(target=run, daemon=True, name="db-months").start()
        return True

    def start_meters(self) -> bool:
        """Start the meter reader for the devices whose connection is kept open (real plant only)."""
        if self.simulated or self.meters is not None:
            return False
        self._meters_wanted = True
        from .meters import MeterReader
        cfg = self.registry.extra.get("meters")
        try:
            r = MeterReader(self, cfg if isinstance(cfg, dict) else {})
        except (TypeError, ValueError) as exc:        # a hand edit gone wrong must never stop the collector
            print(f"[collector] the meters block of the config is not valid ({exc}); the meters are read with the defaults")
            r = MeterReader(self, {})
        if not r.device_ids:
            return False
        self.meters = r
        r.start()
        return True

    def start_live(self) -> bool:
        """Start the live reader when the config (or Settings) asks for it (real plant only; the demo has its own clock)."""
        cfg = self.live_cfg()
        if self.simulated or not cfg.get("enabled") or self.live is not None:
            return False
        from .live import LiveReader
        r = LiveReader(self, cfg)
        if not r.device_ids:
            return False
        self.live = r
        r.start()
        return True

    def set_live_every(self, seconds: int) -> None:
        """Settings: how often the master is read for the live view (0 = not live: read at the site read).
        Applied at once and remembered in settings.json."""
        seconds = int(seconds)
        if seconds not in LIVE_OPTIONS_S:
            raise ValueError(f"the live reading must be one of {list(LIVE_OPTIONS_S)} seconds (0 = off)")
        if self.simulated:
            raise ValueError("the demo has no live reading; this setting applies to the real plant config")
        if seconds and not ((self.registry.extra.get("live") or {}).get("devices")):
            raise ValueError("the config has no live devices (live.devices)")

        def change(data):
            data["live_every_s"] = seconds
            data["changed_at"] = datetime.now(timezone.utc).isoformat()
        self._update_settings(change)
        if seconds == 0:
            old, self.live = self.live, None
            if old is not None:
                old.close()
                if old.thread is not None:
                    old.thread.join(timeout=20)
        elif self.live is None:
            self.start_live()
        else:
            self.live.set_every(seconds)
        self.store.event("settings", "live reading of the master: " + (f"every {seconds} s" if 0 < seconds < 60 else
                         f"every {seconds // 60} min" if seconds else "off (read at the site read)"), severity="info", code="SETTINGS")

    def set_device_every(self, dids: list[str], seconds: int) -> None:
        """Settings: read these devices with every site read (0) or less often. Applied at once, remembered in
        settings.json; it also undoes a slow-down by the live guard for them."""
        seconds = int(seconds)
        if seconds not in DEVICE_OPTIONS_S:
            raise ValueError(f"the reading must be one of {list(DEVICE_OPTIONS_S)} seconds (0 = with every site read)")
        if self.simulated:
            raise ValueError("the demo refreshes every 30 s; this setting applies to the real plant config")
        live = set(self.live.device_ids) if self.live is not None else set()
        for did in dids:
            if did not in self.registry.devices:
                raise ValueError(f"unknown device {did}")
            if did in live:
                raise ValueError(f"{did} is read live; set the master's live reading instead")
        for did in dids:
            self.device_choice[did] = seconds
            if seconds:
                self.every_override[did] = seconds
            else:
                self.every_override.pop(did, None)

        def change(data):
            ch = data.get("device_every_s") if isinstance(data.get("device_every_s"), dict) else {}
            sl = data.get("slowed_devices") if isinstance(data.get("slowed_devices"), dict) else {}
            for did in dids:
                ch[did] = seconds
                sl.pop(did, None)
            data["device_every_s"], data["slowed_devices"] = ch, sl
            data["changed_at"] = datetime.now(timezone.utc).isoformat()
        self._update_settings(change)
        self.store.event("settings", f"{', '.join(dids)}: read " + (f"every {seconds // 60} min" if seconds else "with every site read"),
                         severity="info", code="SETTINGS")

    def turkey_latest(self, did: str) -> dict:
        """The device's values as the Turkey feed shows them: those of the last site read."""
        snap = self.site_snapshot
        if snap is not None:
            return dict(snap.get(did, {}))
        if self.live is not None and did in self.live.device_ids:
            return {}                            # before the first site read: nothing yet, as always (never live values)
        return self.store.latest(did)

    def live_point(self) -> list:
        """[epoch ms, load, solar, gensets, grid, then each genset's share of the gensets (live_columns)] now,
        for the live chart."""
        from .views import series_specs
        specs = series_specs(self.registry)
        out = [int(self.now().timestamp() * 1000)]
        for key in ("load_kw", "pv_kw", "genset_kw", "mains_kw"):
            xs = [self.store.value(d, p) for d, p in specs.get(key, ("sum", []))[1]]
            xs = [float(x) for x in xs if isinstance(x, (int, float)) and not isinstance(x, bool)]
            out.append(round(sum(xs), 1) if xs else None)
        out[4], out[1], _src = self.mains_checked(out[4], out[1])
        out.extend(self.genset_split())
        return out

    def live_columns(self) -> list[str]:
        gs = self.genshares
        return ["t", "load_kw", "pv_kw", "genset_kw", "mains_kw"] + ([f"g{g}_kw" for g in gs.numbers] if gs else [])

    def genset_split(self) -> list:
        """Each genset's share of the master's Gensets P now, in the order of genshares.numbers (see genshare.py:
        the master has no kW per genset; this is its load sharing worked out). [] without a site master."""
        return [e["kw"] for e in self.genset_each()]

    def genset_each(self) -> list[dict]:
        """[{n, kw, kvar}] per genset of the site master: its share of the gensets' P and Q by the load sharing
        (0 off the bus, None when not known). [] without a site master."""
        gs = self.genshares
        if gs is None:
            return []
        if not gs.seeded:
            with self._share_lock:
                if not gs.seeded:
                    try:
                        gs.seed(self.store, *seed_window(self.now()))
                    except Exception as exc:          # the history is only a head start; the live readings teach it too
                        gs.seeded = True
                        print(f"[collector] genset shares: history not read ({exc})")
        m = gs.master_id
        on = {}
        for g in gs.numbers:
            v = self.store.value(m, f"g{g}_running")
            on[g] = None if v is None else bool(v >= 0.5)
        rn = self.store.value(m, "running_nominal_kw")
        sp = gs.split(self.store.value(m, "power_kw"), on, rn)
        q = self.store.value(m, "gensets_q_kvar")
        w = gs.weights(on, rn) if isinstance(q, (int, float)) and not isinstance(q, bool) else None
        if w is None and isinstance(q, (int, float)) and all(on.get(g) is False for g in gs.numbers) and abs(q) < 5:
            w = {g: 0.0 for g in gs.numbers}
        return [{"n": g, "kw": sp[g], "kvar": None if w is None else round(float(q) * w[g], 1)} for g in gs.numbers]

    # --- grid import checked against the incomers (see MAINS_ZERO_KW) -------------------------

    def mains_pairs(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """(the master's Mains Import P, the incomers' power) - both empty unless the site has both."""
        reg = self.registry
        master = [(d.id, "mains_power_kw") for d in reg.devices.values() if d.point("mains_power_kw")]
        inc = [(d.id, "power_kw") for d in reg.by_kind("grid") if d.point("power_kw")]
        return (master, inc) if master and inc else ([], [])

    def incomer_hold_s(self, did: str) -> float:
        """How long a stored reading of `did` counts: its read interval and a half, plus 2 min (the live reader
        stores a value that does not change only every few minutes: for its devices, that interval)."""
        lv, mr = self.live, self.meters
        every = max(lv.every_s, lv.idle_store_s) if lv is not None and did in lv.device_ids else \
            max(mr.every_s, mr.store_every_s) if mr is not None and did in mr.device_ids else self.device_every_s(did)
        return every * 1.5 + 120.0

    def grid_went(self, since: datetime, until: datetime) -> list[float]:
        """When (epoch s, ascending) the master saw the grid go: its Mains Fail alarm coming on, or its
        mains-available bit going off."""
        out = []
        for did, _p in self.mains_pairs()[0]:
            dev = self.registry.devices[did]
            for pid, gone_when in (("alarm_mains_fail", True), ("mains_available", False)):
                if not dev.point(pid):
                    continue
                prev = None
                for t, v in self.store.history(did, pid, since, until):
                    if v is None:
                        continue
                    gone = (float(v) >= 0.5) == gone_when
                    if gone and prev is False:
                        out.append(datetime.fromisoformat(t).timestamp())
                    prev = gone
        return sorted(out)

    def _went_between(self, went: list[float], a: float, b: float) -> bool:
        i = bisect.bisect_right(went, a)
        return i < len(went) and went[i] <= b

    def incomers_now(self) -> tuple[float | None, int, int, float | None]:
        """(the incomers added up from their last good readings that still count, how many counted, how many,
        the age in s of the oldest counted) - (None, 0, n, None) when none counts."""
        _m, inc = self.mains_pairs()
        now, xs, oldest = self.now(), [], None
        for did, pid in inc:
            r = self.store.latest(did).get(pid)
            if r is None or r.quality != Quality.GOOD or isinstance(r.value, bool) or not isinstance(r.value, (int, float)):
                continue
            if (now - r.source_ts).total_seconds() > self.incomer_hold_s(did):
                continue
            xs.append(float(r.value))
            oldest = r.source_ts if oldest is None or r.source_ts < oldest else oldest
        if not xs:
            return None, 0, len(inc), None
        if self._went_between(self.grid_went(oldest - timedelta(seconds=STATE_LOOKBACK_S), now), oldest.timestamp(), now.timestamp()):
            return None, 0, len(inc), None
        return round(sum(xs), 1), len(xs), len(inc), round((now - oldest).total_seconds())

    def incomers_at(self, at: list[float]) -> list[float | None]:
        """The incomers' sum as it stood at each time in `at` (epoch s, ascending), from the stored readings,
        by the same rules as incomers_now; None where no reading counts."""
        _m, inc = self.mains_pairs()
        if not at or not inc:
            return [None] * len(at)
        a, b = datetime.fromtimestamp(at[0], timezone.utc), datetime.fromtimestamp(at[-1], timezone.utc)
        back = max(self.incomer_hold_s(did) for did, _p in inc)
        per = []
        for did, pid in inc:
            hold = self.incomer_hold_s(did)
            rows = [(datetime.fromisoformat(t).timestamp(), float(v))
                    for t, v in self.store.history(did, pid, a - timedelta(seconds=hold), b) if v is not None]
            per.append(carry_forward(rows, at, hold))
        went = self.grid_went(a - timedelta(seconds=back + STATE_LOOKBACK_S), b)
        out: list[float | None] = []
        for i, t in enumerate(at):
            got = [p[i] for p in per if p[i] is not None]
            if not got or self._went_between(went, min(g[0] for g in got), t):
                out.append(None)
            else:
                out.append(sum(g[1] for g in got))
        return out

    def mains_checked(self, mains: float | None, load: float | None) -> tuple[float | None, float | None, dict | None]:
        """(grid import, site load, None) as read - or, when the master's grid reading is not believed, the
        incomers' sum, the load corrected by the difference, and {"incomers": counted, "of": how many,
        "age_s": age of the oldest counted reading}."""
        if not self.mains_pairs()[0] or not mains_suspect(mains, INCOMERS_ON_KW):    # the master reads power: believed
            return mains, load, None
        inc, n, of, age = self.incomers_now()
        if not mains_suspect(mains, inc):
            return mains, load, None
        if load is not None and self.load_from_master():
            load = round(load - mains + inc, 1)
        return inc, load, {"incomers": n, "of": of, "age_s": age}

    def load_from_master(self) -> bool:
        """The site load is the master's (gensets + grid + solar), not a site meter's."""
        return not any(d.point("load_kw") for d in self.registry.by_kind("meter"))

    def mains_correction_kwh(self, start: datetime, end: datetime) -> float:
        """Energy the master's grid reading missed in [start, end): at each of its readings that is not believed,
        the incomers' sum (as it stood then) in its place, integrated the same way as integrate_kwh."""
        master, _inc = self.mains_pairs()
        kwh = 0.0
        for did, pid in master:
            rows = self.store.history(did, pid, start, end)
            if not any(mains_suspect(v, INCOMERS_ON_KW) for _t, v in rows):
                continue
            ts = [datetime.fromisoformat(t).timestamp() for t, _v in rows]
            inc = self.incomers_at(ts)
            fix = [None if v is None else (s - v if mains_suspect(v, s) else 0.0) for (_t, v), s in zip(rows, inc)]
            for i in range(1, len(rows)):          # as integrate_kwh: a pair with a missing value counts nothing
                if fix[i - 1] is not None and fix[i] is not None:
                    kwh += (fix[i - 1] + fix[i]) / 2 * (ts[i] - ts[i - 1]) / 3600
        return round(kwh, 1)

    # --- polling ----------------------------------------------------------------------

    def poll(self) -> None:
        """The site read: every device once (the live reader's devices excepted while it runs, and the slow
        ones only when due), then the Turkey feed. Safe to call from the API thread while the loop runs."""
        with self._poll_lock:
            self._poll()

    def read_everything_now(self, wait_s: float = 8.0) -> dict:
        """"Read devices now": the site read, and at the same time one cycle of the live reader and of the meter
        reader, each in its own thread on its own kept connection (never a second connection to a device). Waits
        up to wait_s for those cycles; a device that answers later still shows up at the next refresh."""
        asked = []
        for name, r in (("live", self.live), ("meters", self.meters)):
            if r is not None and r.running and r.wake_now():
                asked.append((name, r, r.cycles))
        self.poll()
        deadline = time.monotonic() + wait_s
        done = {}
        for name, r, n0 in asked:
            while r.cycles <= n0 and time.monotonic() < deadline:
                time.sleep(0.1)
            done[name] = r.cycles > n0
        return done

    def _due(self, did: str, now_mono: float) -> bool:
        every = self.every_override.get(did)
        if not every:
            return True
        last = self._last_read_mono.get(did)
        return last is None or now_mono - last >= every - 5          # 5 s grace: the site read itself takes time

    def _poll(self) -> None:
        lv = self.live                           # Settings may switch the live reader off meanwhile
        live = lv if (lv is not None and lv.running) else None
        mr = self.meters
        meters = mr if (mr is not None and mr.running) else None
        now_mono = time.monotonic()
        prev_host = None
        for did, ad in self.adapters.items():
            if live is not None and did in live.device_ids and not live.failures_in_row:
                continue                         # read every few seconds by the live reader; its values are current
                                                 # (while it backs off, the site read reads the device itself)
            if did in self.removed:
                continue                         # removed from the dashboard
            if meters is not None and (did in meters.device_ids or (self.registry.devices[did].connection or {}).get("keep_open")):
                continue                         # a kept meter: read by the meter reader on its one connection
            if not self.read_kept and (self.registry.devices[did].connection or {}).get("keep_open"):
                continue                         # read by the meter reader on its kept connection; a second connection
                                                 # would only be ignored by the meters' gateway
            if not self._due(did, now_mono):
                continue                         # a slow device: its last values stay until it is due
            conn = self.registry.devices[did].connection
            host = (conn.get("host"), conn.get("port", 502)) if conn.get("host") else None
            if host and host == prev_host and not self.simulated:
                time.sleep(SAME_HOST_PAUSE_S)    # the ten inverters sit behind one gateway: no burst of requests
            prev_host = host
            answered = True
            try:
                rows = ad.read()
            except Exception as exc:
                answered = False
                d = self.registry.devices[did]
                from .adapters import reading
                rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in d.points}
            if answered and getattr(ad, "last_ok_blocks", 1) > 0:   # the guard counts only reads the device answered
                self.read_times[did] = time.time()
            self._last_read_mono[did] = time.monotonic()
            self.store.ingest(did, rows)
            self._health(did, rows)
        try:                                     # free the gateway's connection slots between polls
            from .adapters.modbus_tcp import close_all
            keep = set(lv.endpoints) if lv is not None and lv is self.live else set()   # the live one stays open,
            if meters is not None:                                                     # and the meters' kept ones
                keep |= set(meters.endpoints)
            close_all(keep=frozenset(keep))
        except Exception:
            pass
        self.site_snapshot = {did: self.store.latest(did) for did in self.registry.devices}
        self.polls += 1
        self.last_poll_at = datetime.now(timezone.utc)
        if lv is None:
            self._sim_history.append(self.live_point())
        for fn in list(self.after_poll):
            try:
                fn()
            except Exception as exc:
                print(f"after_poll callback failed: {exc}")

    def advance(self, seconds: float, step: float = 60.0) -> None:
        """Simulator only: move simulated time forward, polling every `step` seconds."""
        assert self.plant is not None
        left = seconds
        with self.store.bulk():
            while left > 0:
                s = min(step, left)
                self.plant.step(s)
                for ts, kind, dev, sev, code, msg in getattr(self.plant, "drain_events", list)():   # the model's controller messages
                    self.store.event(kind, msg, device_id=dev, severity=sev, code=code, ts=ts)
                left -= s
                self.poll()

    def _health(self, did: str, rows) -> None:
        """Minimal event log: comms lost/restored and controller alarm bits."""
        ts = self.now()
        dev = self.registry.devices[did]
        measured = [r for pid, r in rows.items() if not (dev.point(pid) and dev.point(pid).raw.get("derived"))]
        asked = [r for r in measured if not (r.error or "").startswith("no register mapping")]   # unmapped points were never requested
        all_bad = bool(asked) and all(r.quality == Quality.BAD for r in asked)
        if all_bad and did not in self._comms_lost:
            self._comms_lost.add(did)
            err = next((r.error for r in asked if r.error), "")
            self.store.event("comms_lost", f"{did}: no valid data ({err})", device_id=did, severity="error", code="COMMS_LOST", ts=ts)
        elif not all_bad and did in self._comms_lost:
            self._comms_lost.discard(did)
            self.store.event("comms_restored", f"{did}: data restored", device_id=did, severity="info", code="COMMS_OK", ts=ts)
        av = rows.get("available") if dev.kind == "grid" else rows.get("mains_available")
        if av is not None and av.quality == Quality.GOOD:
            key = f"{did}:grid"
            if not av.value and key not in self._grid_off:
                self._grid_off.add(key)
                self.store.event("grid_lost", f"{did}: grid not available", device_id=did, severity="warning", code="GRID_OFF", ts=ts)
            elif av.value and key in self._grid_off:
                self._grid_off.discard(key)
                self.store.event("grid_restored", f"{did}: grid back", device_id=did, severity="info", code="GRID_OK", ts=ts)
        a = rows.get("alarm_active")
        if a is not None and a.quality == Quality.GOOD:
            if a.value and did not in self._alarm_on:
                self._alarm_on.add(did)
                code = rows.get("alarm_code")
                self.store.event("alarm", f"{did}: alarm active" + (f" (code {code.value})" if code and code.value else ""),
                                 device_id=did, severity="error", code="ALARM", ts=ts)
            elif not a.value and did in self._alarm_on:
                self._alarm_on.discard(did)
                self.store.event("alarm_cleared", f"{did}: alarm cleared", device_id=did, severity="info", code="ALARM_OK", ts=ts)

    def active_issues(self) -> list[dict]:
        out = [{"code": "COMMS_LOST", "device": d, "message": "no valid data"} for d in sorted(self._comms_lost)]
        out += [{"code": "ALARM", "device": d, "message": "controller alarm active"} for d in sorted(self._alarm_on)]
        out += [{"code": "GRID_OFF", "device": k.split(":")[0], "message": "grid not available"} for k in sorted(self._grid_off)]
        return out

    # --- daily totals ----------------------------------------------------------------------

    def daily_totals(self, day: datetime | None = None) -> dict:
        day = (day or self.now()).astimezone(timezone.utc)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        reg, st = self.registry, self.store
        load = sum(st.integrate_kwh(m.id, "load_kw", start, end) for m in reg.by_kind("meter"))
        src = None
        if not reg.by_kind("meter"):              # no site meter (the real plant): the master's site load
            src = next((d for d in reg.devices.values() if d.point("load_kw")), None)
            load = st.integrate_kwh(src.id, "load_kw", start, end) if src else 0.0
        # solar: the master's aggregated reading when there is one (as the dashboard), else the array/gateway
        # totals, else the PV devices one by one - never inverters and their gateway together
        master_pv = next((d for d in reg.devices.values() if d.point("pv_prod_kw")), None)
        pvs = reg.by_kind("pv")
        arrays = [p for p in pvs if p.point("inverters_total")] or pvs
        pv = (st.integrate_kwh(master_pv.id, "pv_prod_kw", start, end) if master_pv else
              sum(st.integrate_kwh(p.id, "power_kw" if p.point("power_kw") else "ac_power_kw", start, end) for p in arrays))
        pv_avail = sum(st.integrate_kwh(p.id, "available_kw", start, end) for p in reg.by_kind("pv"))
        gens = {}
        gen_kwh = fuel = 0.0
        for g in reg.by_kind("genset"):
            kwh = st.integrate_kwh(g.id, "power_kw", start, end)
            rows = st.history(g.id, "fuel_rate_lph", start, end)
            litres = sum((v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                         for (t0, v0), (t1, v1) in zip(rows, rows[1:]) if v0 is not None and v1 is not None)
            run = st.history(g.id, "running", start, end)
            hours = sum((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                        for (t0, v0), (t1, _v1) in zip(run, run[1:]) if v0)
            gens[g.id] = {"energy_kwh": round(kwh, 1), "fuel_l": round(litres, 1), "run_hours": round(hours, 2),
                          "avg_load_kw": round(kwh / hours, 1) if hours > 0.05 else 0.0,
                          "l_per_kwh": round(litres / kwh, 3) if kwh > 1 else None}
            gen_kwh += kwh
            fuel += litres
        # grid: the site master's Mains Import P when there is one (as the dashboard), else the incomers
        master_mains = [d for d in reg.devices.values() if d.point("mains_power_kw")]
        grid_kwh = (sum(st.integrate_kwh(d.id, "mains_power_kw", start, end) for d in master_mains) if master_mains else
                    sum(st.integrate_kwh(g.id, "power_kw", start, end) for g in reg.by_kind("grid")))
        # ... with the incomers in place of the master's reading where that is not believed (see MAINS_ZERO_KW)
        missed = self.mains_correction_kwh(start, end)
        if missed:
            grid_kwh += missed
            if src is not None and src.point("mains_power_kw"):     # the master's site load counts its grid reading in
                load += missed
        grid_hours = 0.0
        for d in reg.devices.values():               # several devices see the same grid: the longest, not the sum
            pid = "available" if d.kind == "grid" else ("mains_available" if d.point("mains_available") else None)
            if pid:
                rows = st.history(d.id, pid, start, end)
                grid_hours = max(grid_hours, sum((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                                                 for (t0, v0), (t1, _v1) in zip(rows, rows[1:]) if v0))
        bess_dis = bess_chg = 0.0
        for b in reg.by_kind("bess"):
            rows = st.history(b.id, "power_kw", start, end)
            for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
                if v0 is None or v1 is None:
                    continue
                e = (v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                bess_dis += max(e, 0); bess_chg += max(-e, 0)
        # what the gensets would have burned without solar: everything the grid did not supply
        counter_l = max(0.0, load - grid_kwh) * 0.30            # ~0.30 L/kWh at typical load
        metered = any(g.point("fuel_rate_lph") for g in reg.by_kind("genset"))
        burnt_l = fuel if metered else gen_kwh * 0.30            # no fuel metering (the real plant): the same estimate
        return {
            "date": start.date().isoformat(), "site": reg.site.name,
            "totals": {
                "load_kwh": round(load, 1), "pv_kwh": round(pv, 1), "pv_available_kwh": round(pv_avail, 1),
                "pv_curtailed_kwh": round(max(0.0, pv_avail - pv), 1),
                "pv_share_pct": round(100 * pv / load, 1) if load else None,
                "bess_discharged_kwh": round(bess_dis, 1), "bess_charged_kwh": round(bess_chg, 1),
                "genset_kwh": round(gen_kwh, 1), "fuel_l": round(fuel, 1),
                "fuel_cost": round(fuel * reg.site.fuel_price_per_l, 2),
                "fuel_saved_vs_no_solar_l": round(max(0.0, counter_l - burnt_l), 1), "fuel_metered": metered,
                "grid_kwh": round(grid_kwh, 1), "grid_hours": round(grid_hours, 1),
                "grid_share_pct": round(100 * grid_kwh / load, 1) if load else None,
            },
            "gensets": gens,
            "events": [e for e in st.events(start, limit=300) if e["ts"] < end.isoformat()],
        }


def run_loop(c: Collector, interval: float | None = None, stop=None) -> None:
    """Poll whenever the last read is older than the current interval. The interval is read
    every second, so a change from the dashboard takes effect at once; a manual "read now"
    counts as a read and pushes the next scheduled one."""
    fixed = float(interval) if interval else None
    while not (stop and stop.is_set()):
        every = fixed if fixed else c.poll_interval_s
        last = c.last_poll_at.timestamp() if c.last_poll_at else 0.0
        if time.time() - last >= every:
            try:
                if c.simulated:
                    c.advance(every, step=every)
                else:
                    c.poll()
            except Exception as exc:
                print(f"collector error: {exc}")
        time.sleep(1.0)
