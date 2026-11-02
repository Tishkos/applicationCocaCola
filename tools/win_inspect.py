"""Lists the windows and controls of a running InteliConfig (or any program), the first step towards driving
its read-only chores (connect, export, save as) from a script. Windows only, with the program open:

    pip install pywinauto
    python tools\\win_inspect.py InteliConfig

Writes the control tree of every window whose title contains the given text to data\\win_inspect_<text>.txt.
It reads the UI tree and clicks nothing. Whether a later script can drive the program at all depends on what
this tree shows: menus, buttons and lists with names are drivable; a canvas without named elements is not.
"""

import os
import sys


def main() -> int:
    try:
        from pywinauto import Desktop
    except ImportError:
        print("pywinauto is not installed: pip install pywinauto")
        return 2
    part = sys.argv[1] if len(sys.argv) > 1 else "InteliConfig"
    os.makedirs("data", exist_ok=True)
    out = os.path.join("data", f"win_inspect_{part}.txt")
    found = 0
    with open(out, "w", encoding="utf-8") as f:
        for w in Desktop(backend="uia").windows():
            title = w.window_text()
            if part.lower() not in title.lower():
                continue
            found += 1
            f.write(f"===== {title}  (handle {w.handle})\n")
            try:
                import contextlib
                import io
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    w.print_control_identifiers(depth=8)
                f.write(buf.getvalue() + "\n")
            except Exception as exc:
                f.write(f"could not read the tree: {exc}\n")
    print(f"{found} window(s) matching '{part}'; tree written to {out}")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
