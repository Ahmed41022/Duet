# PyInstaller one-folder desktop build. Run from the repository root.
from pathlib import Path
import PyInstaller

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
STATIC = SRC / "duet" / "server" / "static"
PYINSTALLER_COPYING = next(
    Path(PyInstaller.__file__).parent.parent.glob(
        "pyinstaller-*.dist-info/licenses/COPYING.txt"
    )
)

a = Analysis(
    [str(ROOT / "packaging" / "desktop_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(STATIC), "duet/server/static"),
        (str(ROOT / "LICENSE"), "licenses"),
        (str(ROOT / "packaging" / "THIRD_PARTY_NOTICES.txt"), "licenses"),
        (str(PYINSTALLER_COPYING), "licenses"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="duet-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="duet-desktop",
)
