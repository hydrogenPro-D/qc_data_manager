# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\Users\\CsengerMolnár\\OneDrive - HydrogenPro ASA\\Dokumenter\\GitHub\\qc_data_manager\\main.py'],
    pathex=[],
    binaries=[],
    datas=[('../assets/app_icon.ico', 'assets'),
           ('../assets/app_icon.png', 'assets')],
    hiddenimports=['PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QC Data Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows needs the .ico for the exe. The .png is shipped too, in
    # COLLECT below, because anything that scales the artwork wants it.
    icon='../assets/app_icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QC Data Manager',
)
