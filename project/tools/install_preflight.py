#!/usr/bin/env python3
"""Read-only public VinylPi installation preflight. Never prints environment values."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
APPS = {"pi_epaper": "pi_epaper", "music-bridge": "music-bridge", "vinylpi-dashboard": "vinylpi-dashboard"}
BINARIES = ("python3", "ffmpeg", "arecord", "aplay", "darkice", "icecast2")


def check_tree(root):
    results = []
    for label, folder in APPS.items():
        for rel in (["requirements.txt"] if label != "pi_epaper" else ["requirements.txt", "display_service.py"]):
            p = root / folder / rel
            results.append((p.is_file(), f"source: {folder}/{rel}"))
    results.append(((root / "firmware/CrowPanel_Remote_BMP/CrowPanel_Remote_BMP.ino").is_file(), "firmware: CrowPanel sketch"))
    results.append(((root / "vinylpi-dashboard/dashboard/static").is_dir(), "dashboard static directory"))
    for executable in BINARIES:
        results.append((shutil.which(executable) is not None, f"installed command: {executable}"))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    results = check_tree(args.root)
    for ok, label in results:
        print(("PASS" if ok else "MISSING") + "  " + label)
    print("Note: command presence does not validate service availability or correctness.")
    print("No files or services were modified; secret/config values were not read.")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
