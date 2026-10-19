#!/usr/bin/env python3
"""
One-command build: exported definitions -> Excel inventory + interactive lineage explorer.

Needs only Python 3.8+ (no pip install): the Excel library is bundled in scripts/_vendor.

  python inventory.py <export file or .sql folder> [--datamart NAME] [--client NAME] [--target databricks|fabric]

<export file>  the saved results of assets/export_definitions.sql (.json, .csv or .xlsx)
<.sql folder>  a SQL Database Project / sqlpackage / Generate Scripts folder

Writes to output/<datamart>/ under the current folder:
  <datamart>_Object_Inventory.xlsx, <datamart>_Object_Inventory_Lineage.html, inventory.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


def run(args):
    print("> " + " ".join(f'"{a}"' if " " in a else a for a in args), flush=True)
    res = subprocess.run(args, capture_output=True, text=True)
    if res.stdout:
        print(res.stdout.rstrip())
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        sys.exit(res.returncode)
    return json.loads(res.stdout) if res.stdout.strip().startswith("{") else {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="+", help="export file(s) (.json/.csv/.xlsx) or a folder of .sql scripts")
    ap.add_argument("--datamart", help="name used in the report and file names (default: from the file name)")
    ap.add_argument("--client")
    ap.add_argument("--target", choices=["databricks", "fabric"], default="databricks")
    ap.add_argument("--out-dir", default="output", help="root output folder (default: ./output)")
    ap.add_argument("--mirror-pattern", help="regex for Databricks mirror objects (see SKILL.md)")
    ap.add_argument("--open", action="store_true", help="open the lineage explorer in the browser when done")
    a, extra = ap.parse_known_args()   # anything unrecognised is passed on to build_report.py

    srcs = [os.path.abspath(x) for x in a.source]
    for x in srcs:
        if not os.path.exists(x):
            sys.exit(f"Not found: {x}")
    src = srcs[0]
    is_folder = len(srcs) == 1 and os.path.isdir(src) and any(
        f.lower().endswith(".sql") for _, _, fs in os.walk(src) for f in fs)
    stem = os.path.splitext(os.path.basename(src.rstrip("/\\")))[0]
    name = a.datamart or re.sub(r"[_\- ]*(long[_\- ]*)?(definitions?|export|results?)$", "", stem, flags=re.I) or stem
    safe = re.sub(r"[^\w\-]+", "_", name)
    out_dir = os.path.join(os.path.abspath(a.out_dir), safe)
    os.makedirs(out_dir, exist_ok=True)
    inv = os.path.join(out_dir, "inventory.json")
    xlsx = os.path.join(out_dir, f"{safe}_Object_Inventory.xlsx")

    py = sys.executable
    cmd = [py, os.path.join(HERE, "extract.py"), "folder" if is_folder else "export", "--path"] + \
        ([src] if is_folder else srcs) + ["--datamart", name, "--out", inv]
    if a.mirror_pattern:
        cmd += ["--mirror-pattern", a.mirror_pattern]
    ext = run(cmd)
    if not ext.get("objects"):
        sys.exit("No objects were read - check the file is the saved result of export_definitions.sql.")

    cmd = [py, os.path.join(HERE, "build_report.py"), "--in", inv, "--out", xlsx, "--target", a.target]
    if a.client and a.client.strip():
        cmd += ["--client", a.client.strip()]
    rep = run(cmd + [x for x in extra if x != "--"])

    print("\nDone.")
    print(f"  Excel report    : {xlsx}")
    if rep.get("lineage_html"):
        print(f"  Lineage explorer: {rep['lineage_html']}")
    for w in ext.get("warnings", []):
        print(f"  warning: {w}")
    for n in rep.get("notes", []):
        print(f"  note: {n}")
    if a.open and rep.get("lineage_html"):
        from pathlib import Path
        webbrowser.open(Path(rep["lineage_html"]).resolve().as_uri())


if __name__ == "__main__":
    main()
