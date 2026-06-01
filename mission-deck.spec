# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for mission-deck — builds a single windowed EXE.

Build with:   pyinstaller mission-deck.spec       (or run build.ps1)
Output:       dist/mission-deck.exe

Notes
-----
* ``config.example.json`` is bundled so the "Explore Demo Data" option works in
  a fresh install. The user's real ``config.json`` is NOT bundled — it lives
  next to the EXE or in %APPDATA%/mission-deck, and is remembered across runs.
* CustomTkinter ships theme/asset JSON files that must be collected explicitly,
  otherwise the packaged app fails to start.
"""

from PyInstaller.utils.hooks import collect_data_files

datas = [("config.example.json", ".")]
datas += collect_data_files("customtkinter")

a = Analysis(
    ["mission_deck/__main__.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["customtkinter"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="mission-deck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,          # windowed app (no console window)
    disable_windowed_traceback=False,
    icon=None,              # add an .ico path here to brand the EXE
)
