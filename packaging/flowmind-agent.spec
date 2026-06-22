# PyInstaller spec for the FlowMind Agent (one-file executable).
# Build from the repo root:  pyinstaller --clean --noconfirm packaging/flowmind-agent.spec
#
# Notes on the tricky bits:
#  * agent/, utils/, browser_bridge/ are shipped as SOURCE (datas) because the
#    executor imports plugins dynamically by top-level name (`plugins.<id>`); the
#    entry adds these dirs to sys.path so the imports resolve from the unpacked
#    bundle at runtime.
#  * Because those imports are dynamic, PyInstaller can't see the plugins' own
#    dependencies — they're pulled in explicitly via collect_all / hiddenimports.
from PyInstaller.utils.hooks import collect_all

datas = [
    ("agent", "agent"),
    ("utils", "utils"),
    ("browser_bridge", "browser_bridge"),
]
binaries = []
hiddenimports = [
    "websockets",
    "yaml",
    "dotenv",
    "requests",
    "bs4",
    "httpx",
    "aiosqlite",
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.base",
    "email.mime.application",
]

# Bundle full packages the plugins / admin API need (data files + submodules).
for pkg in ("fastapi", "uvicorn", "starlette", "anyio", "pydantic", "openpyxl", "docx"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["packaging/flowmind_agent.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="flowmind-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
