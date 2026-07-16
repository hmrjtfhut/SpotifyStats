#!/usr/bin/env python3
"""Build a single-file Windows .exe with PyInstaller.

Run from anywhere inside the project folder:
    python build_exe.py
"""
import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# Clean old build artefacts
subprocess.run(
    ["powershell", "-Command",
     "rm -Recurse -Force build, dist -ErrorAction SilentlyContinue"],
    check=False,
)

# Generate the app icon from the same artwork used in the tray so the .exe
# doesn't end up with the blank default PyInstaller icon.
icon_path = os.path.join(PROJECT_DIR, "_app_icon.ico")
try:
    from tray import TrayIcon
    if TrayIcon.write_ico(icon_path):
        print(f"Icon written to {icon_path}")
    else:
        icon_path = None
except Exception as e:
    print(f"Could not generate icon (building without one): {e}")
    icon_path = None

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "SpotifyStatsTracker",
    # winsdk hidden imports
    "--hidden-import=winsdk",
    "--hidden-import=winsdk.windows.media",
    "--hidden-import=winsdk.windows.media.control",
    "--hidden-import=winsdk.windows.foundation",
    "--hidden-import=winsdk.windows.storage.streams",
    "--hidden-import=asyncio",
    # Pillow backend
    "--hidden-import=PIL._tkinter_finder",
]
if icon_path and os.path.exists(icon_path):
    cmd += ["--icon", icon_path]
cmd.append("main.py")

print("Building EXE …")
result = subprocess.run(cmd, capture_output=True, text=True)

if result.returncode == 0:
    exe = os.path.join(PROJECT_DIR, "dist", "SpotifyStatsTracker.exe")
    if os.path.exists(exe):
        size = os.path.getsize(exe) / (1024 * 1024)
        print(f"BUILD SUCCESSFUL — {exe}  ({size:.1f} MB)")
    else:
        print("BUILD SUCCESSFUL (exe not found at expected path)")
else:
    print("BUILD FAILED")
    print(result.stderr[-4000:])
    sys.exit(1)
