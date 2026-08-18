from pathlib import Path

project_root = Path(SPEC).resolve().parent.parent
runtime_root = project_root / "runtime" / "python"

a = Analysis(
    [str(runtime_root / "mia_runtime.py")],
    pathex=[str(runtime_root)],
    binaries=[],
    datas=[(str(runtime_root / "migrations"), "migrations")],
    hiddenimports=[],
    hookspath=[],
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
    name="mia-runtime",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="mia-runtime",
)
