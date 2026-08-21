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
        # Server admission, multi-slot worker-host and HTTP schema modules are
        # intentionally absent from the local desktop execution graph. Local
        # shims are installed before mia_source_backend is imported.
        "app.job_engine.factory",
        "app.job_engine.admission",
        "app.job_engine.admission_safe",
        "app.job_engine.worker",
        "app.external_api.models",
        "pydantic",
        # The pre-refactor desktop crawler spawned its own worker thread and
        # duplicated source login/crawl behavior. ProductionBackend installs a
        # disabled import shim and uses only the source-managed worker/session.
        "mia_crawler",
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
