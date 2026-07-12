from pathlib import Path


service_root = Path(SPECPATH).resolve()
repo_root = service_root.parents[1]

excluded_modules = [
    'IPython',
    'jupyter',
    'notebook',
    'peft',
    'torch',
    'transformers',
    'unsloth',
]

hidden_imports = [
    'uvicorn.lifespan.off',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.utils',
]

resource_names = [
    'campaign_catalog_v1.json',
    'curriculum_v1.json',
    'procedure_registry_v1.json',
    'story_templates_v1.json',
]
runtime_resources = [
    (
        str(service_root / 'resources' / name),
        'services/wayline_forge/resources',
    )
    for name in resource_names
]

a = Analysis(
    [str(service_root / 'app' / 'packaged_launcher.py')],
    pathex=[str(repo_root)],
    binaries=[],
    datas=runtime_resources,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WaylineForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch='arm64',
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
    name='WaylineForge',
)
