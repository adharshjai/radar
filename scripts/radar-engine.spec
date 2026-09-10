# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: python3 -m PyInstaller scripts/radar-engine.spec --distpath engine-build/dist --workpath engine-build/work
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(root, "radar", "templates"), "radar/templates"), (os.path.join(root, "radar", "static"), "radar/static")]
datas += collect_data_files("msal")
hidden = ["radar", "radar.cli", "radar.web", "radar.api", "radar.sync", "radar.sources.canvas", "radar.sources.canvas_session",
          "radar.sources.canvas_feeds", "radar.sources.outlook", "radar.sources.applemail", "radar.enrich.ai", "demo", "demo.fixtures",
          "browser_cookie3", "lz4", "lz4.block", "Cryptodome", "Cryptodome.Cipher.AES", "Cryptodome.Protocol.KDF",
          "pypdf", "msal", "flask", "jinja2", "werkzeug", "requests", "sqlite3", "zoneinfo"]
hidden += ["anthropic", "anthropic.types", "httpx2", "pydantic", "pydantic_core", "jiter", "anyio", "sniffio"]

a = Analysis([os.path.join(root, "scripts", "radar_engine_entry.py")], pathex=[root], binaries=[], datas=datas,
             hiddenimports=hidden, hookspath=[], runtime_hooks=[], excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy",
             "IPython", "notebook", "PyQt5", "PySide2", "torch", "PIL", "botocore", "boto3", "s3transfer", "awscrt",
             "google", "mypy", "lxml", "unittest", "pydoc_data", "xmlrpc"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="radar-engine", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=True, target_arch=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="radar-engine")
