# -*- mode: python ; coding: utf-8 -*-
import os


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('cars.json', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Drop the UCRT redistributable shims some CPython builds (3.13.6+) ship inside the
# interpreter directory: api-ms-win-*.dll plus ucrtbase.dll. They are forwarders to
# libraries every supported Windows (10+) already has, so they add ~1 MB of dead weight
# and — worse — a bundled copy is preferred over the system one, which is exactly how a
# stale msvcp_win/ucrt pair broke startup once already (see the d3dcompiler_47 fix in
# 5626765). Filtering here keeps the package identical whether or not the build machine's
# Python happens to carry them.
_UCRT_SHIMS = ('ucrtbase.dll',)


def _is_ucrt_shim(entry):
    name = os.path.basename(entry[0]).lower()
    return name.startswith('api-ms-win-') or name in _UCRT_SHIMS


a.binaries = TOC([b for b in a.binaries if not _is_ucrt_shim(b)])

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FH6LiveryViewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
