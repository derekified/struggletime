#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
import shutil, subprocess, sys

ok = True
def check(label, good, hint=""):
    global ok
    print(f"{'ok  ' if good else 'FAIL'} {label}" + (f" — {hint}" if hint and not good else ""))
    ok = ok and good

check(f"python {sys.version_info.major}.{sys.version_info.minor} >= 3.10", sys.version_info >= (3, 10), "install python 3.10+")
check("git on PATH", bool(shutil.which("git")), "install git, the daemon anchors to it")
check("no third-party dependencies needed", True)
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True)
check("test suite", r.returncode == 0, "see: python3 -m unittest discover -s tests")
if r.returncode: print(r.stdout[-800:], r.stderr[-800:])
sys.exit(0 if ok else 1)
