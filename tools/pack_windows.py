# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Zip the Nuitka standalone directory into a versioned Windows release archive.

    uv run python tools/pack_windows.py

Nuitka names the folder after the entry script, ``wcl_replay_entry.dist``.
The ``.dist`` suffix is only that label. The executable loads DLLs from the
directory it sits in, so renaming the folder does not change how it starts.
The archive root is ``wcl-replay-<version>`` so a release unpacks under that
name. The zip stays in ``build/`` and survives the next ``build_windows.py``
run, which deletes ``build/nuitka/``.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from wcl_replay import __version__

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "build" / "nuitka" / "wcl_replay_entry.dist"
OUT = ROOT / "build"
EXE_NAME = "wcl-replay.exe"


def archive_name(version: str) -> str:
    safe = version.replace("/", "-").replace("\\", "-").strip() or "0"
    return f"wcl-replay-{safe}-windows.zip"


def archive_root(version: str) -> str:
    safe = version.replace("/", "-").replace("\\", "-").strip() or "0"
    return f"wcl-replay-{safe}"


def pack_dist(dist: Path, dest: Path, version: str) -> Path:
    exe = dist / EXE_NAME
    if not exe.is_file():
        raise SystemExit(f"missing executable: {exe}")
    root_name = archive_root(version)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in dist.rglob("*") if item.is_file()):
            relative = path.relative_to(dist).as_posix()
            archive.write(path, f"{root_name}/{relative}")
    partial.replace(dest)
    return dest


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    dest = OUT / archive_name(__version__)
    pack_dist(DIST, dest, __version__)
    size_mib = dest.stat().st_size / (1024 * 1024)
    print(f"archive: {dest}")
    print(f"size: {size_mib:.1f} MiB")
    print(f"root: {archive_root(__version__)}/")


if __name__ == "__main__":
    main()
