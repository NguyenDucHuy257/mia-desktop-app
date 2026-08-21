from pathlib import Path

project_root = Path(SPEC).resolve().parent.parent
runtime_root = project_root / "runtime" / "python"
vendor_root = runtime_root / "vendor" / "mia_crawl_service"

a = Analysis(
    [str(runtime_root / "mia_runtime.py")],
    pathex=[str(runtime_root), str(vendor_root)],
    binaries=[],
    datas=[
        (str(runtime_root / "migrations"), "migrations"),
        # Vendored modules are frozen as top-level ``app``; their unchanged
        # resource lookup therefore resolves from the bundle root.
        (str(vendor_root / "resources"), "resources"),
        (str(vendor_root / "VENDOR-MANIFEST.json"), "vendor/mia_crawl_service"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[str(runtime_root / "torch_runtime_hook.py")],
    excludes=[
        "setuptools",
        "distutils",
        "pkg_resources",
        # Server B admission/worker-pool modules are intentionally absent from
        # the local desktop execution graph. mia_backend supplies a local
        # app.job_engine.factory shim before mia_source_backend is imported.
        "app.job_engine.factory",
        "app.job_engine.admission",
        "app.job_engine.admission_safe",
        "app.job_engine.postgres_repository",
    ],
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
