"""The Turkey Modbus feed's switch in the settings file (publish.modbus_tcp.enabled in site.plant.yaml).

The Setup ticks or unticks it (scripts\\install.ps1 runs `turkey on|off`), so nobody has to open the settings file by
hand. The file is edited line by line: only the value of `enabled:` changes, every comment and every other line
stays as it is (a YAML library would drop the comments). When the file has no publish block yet, one is added at
the end with the plant's defaults (every interface, port 502, unit 1).
"""
from __future__ import annotations

import re

_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")
DEFAULT_BLOCK = (
    "\n# Modbus TCP feed for the Turkey platform (read-only; the plant's values re-served from this PC).\n"
    "publish:\n"
    "  modbus_tcp:\n"
    "    enabled: {enabled}\n"
    "    bind: 0.0.0.0            # every interface of this PC (set the VPN address to show it there only)\n"
    "    port: 502\n"
    "    unit_id: 1\n"
    "    bad_value: last          # what a tag shows while its device does not answer: last | zero | nan\n"
)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _key(line: str) -> str | None:
    """The mapping key a YAML line starts with ("publish" for "publish:", "enabled" for "  enabled: false  # x")."""
    m = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:(?:\s|$)", line)
    return m.group(1) if m else None


def _block(lines: list[str], start: int) -> int:
    """The index after the last line of the block that starts at lines[start] (its more-indented lines)."""
    base = _indent(lines[start])
    i = start + 1
    while i < len(lines):
        s = lines[i]
        if s.strip() and not s.lstrip().startswith("#") and _indent(s) <= base:
            break
        i += 1
    return i


def _find(lines: list[str], key: str, start: int, end: int, indent: int | None) -> int | None:
    for i in range(start, end):
        if _key(lines[i]) == key and (indent is None or _indent(lines[i]) == indent):
            return i
    return None


def _locate(lines: list[str]) -> tuple[int | None, int | None, int | None]:
    """(publish line, modbus_tcp line, enabled line) indexes, None for what is not there."""
    pub = _find(lines, "publish", 0, len(lines), 0)
    if pub is None:
        return None, None, None
    pend = _block(lines, pub)
    mb = _find(lines, "modbus_tcp", pub + 1, pend, None)
    if mb is None:
        return pub, None, None
    mend = _block(lines, mb)
    en = _find(lines, "enabled", mb + 1, mend, None)
    return pub, mb, en


def _value(line: str) -> str:
    m = re.match(r"^\s*[\w-]+\s*:\s*([^#\s]*)", line)
    return (m.group(1) if m else "").strip().strip("'\"").lower()


def is_enabled(text: str) -> bool | None:
    """True / False as the file says, None when the file has no switch (the program then treats it as off)."""
    lines = text.splitlines()
    _pub, _mb, en = _locate(lines)
    if en is None:
        return None
    v = _value(lines[en])
    return True if v in _TRUE else False if v in _FALSE else None


def set_enabled(text: str, on: bool) -> str:
    """The settings text with publish.modbus_tcp.enabled set to `on`; everything else untouched."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    want = "true" if on else "false"
    pub, mb, en = _locate(lines)
    if en is not None:
        line = lines[en]
        m = re.match(r"^(\s*enabled\s*:\s*)([^#]*?)(\s*#.*)?$", line)
        lines[en] = f"{m.group(1)}{want}{m.group(3) or ''}" if m else re.sub(r":.*$", f": {want}", line, count=1)
    elif mb is not None:
        ind = " " * (_indent(lines[mb]) + 2)
        lines.insert(mb + 1, f"{ind}enabled: {want}")
    elif pub is not None:
        ind = " " * (_indent(lines[pub]) + 2)
        lines[pub + 1:pub + 1] = [f"{ind}modbus_tcp:", f"{ind}  enabled: {want}", f"{ind}  bind: 0.0.0.0",
                                  f"{ind}  port: 502", f"{ind}  unit_id: 1"]
    else:
        lines.extend(DEFAULT_BLOCK.format(enabled=want).splitlines())
    out = nl.join(lines)
    if text.endswith(("\n", "\r\n")) or not text:
        out += nl
    return out


def apply(path: str, on: bool) -> bool:
    """Switch the feed in the file at `path`; True when the file changed. Written in place, same line endings."""
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    new = set_enabled(text, on)
    if new == text:
        return False
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    return True


def state(path: str) -> bool | None:
    with open(path, encoding="utf-8-sig") as f:
        return is_enabled(f.read())
