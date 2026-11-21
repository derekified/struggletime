#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Assert YAML/schema mirrors match verify.py allowlists (CI guard)."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import verify

def main():
    schema = json.loads((ROOT / "schemas" / "heartbeat.schema.json").read_text())
    props = schema["properties"]
    top = set(props) | set(schema.get("required") or [])
    missing_top = verify.ALLOWED_TOP - top
    # signature fields optional in required list
    if missing_top:
        print("schema missing top keys:", sorted(missing_top))
        return 1
    counts = set((props.get("counts") or {}).get("properties") or {})
    missing_c = verify.ALLOWED_COUNTS - counts
    if missing_c:
        print("schema missing counts:", sorted(missing_c))
        return 1
    yaml_path = ROOT / "weighting" / "rules-v0.4.0.yaml"
    text = yaml_path.read_text()
    if verify.FORMULA_VERSION not in text:
        print("YAML formula_version mismatch")
        return 1
    if "AI_STRUGGLE" not in text or "silent_full_secs: 300" not in text:
        print("YAML missing AI_STRUGGLE / silent decay")
        return 1
    # deprecate old cliff file pointer
    old = ROOT / "weighting" / "rules-v0.1.0.yaml"
    if old.exists():
        banner = old.read_text().splitlines()[0] if old.read_text() else ""
        if "DEPRECATED" not in banner:
            old.write_text(
                "# DEPRECATED — see rules-v0.4.0.yaml (verify.py is source of truth)\n"
                + old.read_text()
            )
    print("ok: schema + YAML aligned with verify.py", verify.FORMULA_VERSION)
    return 0

if __name__ == "__main__":
    sys.exit(main())
