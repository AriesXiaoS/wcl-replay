# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Build a standalone Windows directory with Nuitka.

    uv run --no-dev --group package python tools/build_windows.py
    uv run --no-dev --group package python tools/build_windows.py --console

The result is a directory under build/nuitka/, not a single-file executable.
One-file mode unpacks on every launch. Qt WebEngine, QML, and other unused
Qt modules are left out; platforms and styles stay so the window can open.
The executable icon is src/wcl_replay/assets/wcl-replay.ico.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from wcl_replay import __version__

ROOT = Path(__file__).resolve().parents[1]
BOSSES = ROOT / "src" / "wcl_replay" / "bosses"
COMPILED_PACKAGES = BOSSES / "_compiled_packages.py"
OUT = ROOT / "build" / "nuitka"
ASSETS = ROOT / "src" / "wcl_replay" / "assets"

# Sensible Qt plugins include these. platforms (qwindows.dll) and styles stay.
NOINCLUDE_QT_PLUGINS = (
    "imageformats",
    "iconengines",
    "mediaservice",
    "printsupport",
    "tls",
    "multimedia",
    "qml",
    "sqldrivers",
    "networkinformation",
    "generic",
    "sceneparsers",
    "renderers",
    "geoservices",
    "position",
    "sensors",
    "texttospeech",
    "webview",
    "virtualkeyboard",
    "assetimporters",
    "canbus",
    "scxmldatamodel",
)

NOFOLLOW = (
    "tkinter",
    "unittest",
    "numpy.tests",
    "numpy.f2py",
    "numpy.distutils",
    "numpy.typing.tests",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtRemoteObjects",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtHttpServer",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtScxml",
    "PySide6.QtSvgWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtDBus",
    "PySide6.QtXml",
    "PySide6.QtConcurrent",
    "PySide6.QtPrintSupport",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtLocation",
    "PySide6.QtWebView",
    "PySide6.QtNetworkAuth",
)

# Matched against the dist-relative dest path. Qt6Core / Qt6Gui / Qt6Widgets stay.
NOINCLUDE_DLLS = (
    "*Qt6WebEngine*.dll",
    "*QtWebEngineProcess.exe",
    "*Qt6Qml*.dll",
    "*Qt6Quick*.dll",
    "*Qt63D*.dll",
    "*Qt6Multimedia*.dll",
    "*Qt6Pdf*.dll",
    "*opengl32sw.dll",
    "*libEGL.dll",
    "*libGLESv2.dll",
    "*d3dcompiler_*.dll",
)


def boss_package_names() -> tuple[str, ...]:
    names = [
        child.name
        for child in sorted(BOSSES.iterdir())
        if child.is_dir() and not child.name.startswith("_") and (child / "__init__.py").is_file()
    ]
    return tuple(names)


def compiled_packages_source(names: tuple[str, ...]) -> str:
    quoted = [f'"{name}"' for name in names]
    assignment = (
        f"NAMES: tuple[str, ...] = ({', '.join(quoted)},)" if names else "NAMES: tuple[str, ...] = ()"
    )
    if len(assignment) > 110:
        lines = "\n".join(f"    {item}," for item in quoted)
        assignment = f"NAMES: tuple[str, ...] = (\n{lines}\n)"
    return (
        "# Copyright (c) 2026 伐竹取道 (AriesXiao)\n"
        "# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0\n"
        "\n"
        '"""Boss packages included when pkgutil cannot see compiled modules.\n'
        "\n"
        "Written by tools/build_windows.py. Development still discovers packages with\n"
        "pkgutil; a Nuitka standalone build falls back to this tuple.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        f"{assignment}\n"
    )


def write_compiled_packages() -> None:
    COMPILED_PACKAGES.write_text(
        compiled_packages_source(boss_package_names()), encoding="utf-8", newline="\n"
    )


def file_version(version: str) -> str:
    parts = [part for part in version.split(".") if part.isdigit()]
    if not parts:
        parts = ["0"]
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])


def staging_dir() -> Path:
    """MSVC's linker response file cannot write this checkout's Chinese path.

    Compile into an ASCII directory, then copy the finished dist back.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    if local:
        candidates.append(Path(local) / "wcl-replay" / "nuitka")
    candidates.append(Path(r"C:\wcl-replay-nuitka"))
    for candidate in candidates:
        if str(candidate).isascii():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
    raise SystemExit("no ASCII directory available for the MSVC linker")


def staged_assets(staging: Path) -> Path:
    """Copy the icon onto an ASCII path.

    The resource compiler and the linker both receive this path. The checkout
    itself lives under a Chinese directory, which those tools mishandle.
    """
    icon = ASSETS / "wcl-replay.ico"
    if not icon.is_file():
        raise SystemExit(f"missing application icon: {icon}")
    dest = staging / "app-assets"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ASSETS, dest)
    return dest


def nuitka_command(console: bool, output_dir: Path, assets: Path) -> list[str]:
    version = file_version(__version__)
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=standalone",
        "--assume-yes-for-downloads",
        "--msvc=latest",
        # depends.exe corrupts non-ASCII paths. This repo lives under a Chinese directory.
        "--experimental=force-dependencies-pefile",
        "--enable-plugin=pyside6",
        "--include-package=wcl_replay",
        "--include-package-data=certifi",
        "--include-qt-plugins=platforms",
        "--include-qt-plugins=styles",
        "--noinclude-qt-translations",
        "--include-windows-runtime-dlls=yes",
        f"--windows-console-mode={'force' if console else 'disable'}",
        "--output-dir=" + str(output_dir),
        "--output-filename=wcl-replay.exe",
        "--product-name=WCL Replay",
        "--file-description=Warcraft Logs / combat log replay",
        "--company-name=伐竹取道 (AriesXiao)",
        "--copyright=Copyright (c) 2026 伐竹取道 (AriesXiao)",
        f"--file-version={version}",
        f"--product-version={version}",
        f"--windows-icon-from-ico={assets / 'wcl-replay.ico'}",
        f"--include-data-dir={assets}=wcl_replay/assets",
        "--python-flag=no_docstrings",
        "--report=" + str(output_dir / "compilation-report.xml"),
    ]
    cmd.extend(f"--noinclude-qt-plugins={name}" for name in NOINCLUDE_QT_PLUGINS)
    cmd.extend(f"--nofollow-import-to={name}" for name in NOFOLLOW)
    cmd.extend(f"--noinclude-dlls={pattern}" for pattern in NOINCLUDE_DLLS)
    cmd.append("tools/wcl_replay_entry.py")
    return cmd


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def summarize(dist: Path) -> None:
    size_mib = directory_size(dist) / (1024 * 1024)
    exe = dist / "wcl-replay.exe"
    print(f"output: {exe}")
    print(f"size: {size_mib:.1f} MiB")
    qwindows = next(dist.rglob("qwindows.dll"), None)
    if qwindows is not None:
        families = sorted(path.name for path in qwindows.parent.parent.iterdir() if path.is_dir())
        print("qt plugins:", ", ".join(families))
    webengine = sorted(path.name for path in dist.rglob("*") if "webengine" in path.name.lower())
    if webengine:
        print("webengine files:", ", ".join(webengine))
    else:
        print("webengine files: none")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the Windows standalone directory with Nuitka.")
    parser.add_argument("--console", action="store_true", help="keep a console window on the executable")
    args = parser.parse_args()

    write_compiled_packages()
    staging = staging_dir()
    staged_dist = staging / "wcl_replay_entry.dist"
    if staged_dist.exists():
        shutil.rmtree(staged_dist)

    command = nuitka_command(args.console, staging, staged_assets(staging))
    print(" ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)

    if not staged_dist.is_dir():
        raise SystemExit(f"no dist directory under {staging}")
    if OUT.exists():
        shutil.rmtree(OUT)
    dist = OUT / staged_dist.name
    shutil.copytree(staged_dist, dist)
    report = staging / "compilation-report.xml"
    if report.is_file():
        shutil.copy2(report, OUT / report.name)
    shutil.copy2(ROOT / "LICENSE", dist / "LICENSE")
    summarize(dist)


if __name__ == "__main__":
    main()
