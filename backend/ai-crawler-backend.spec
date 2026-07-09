# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 스펙: 백엔드를 단일 폴더(onedir) 실행파일로 번들.
# Windows 빌드 머신에서 실행:  pyinstaller ai-crawler-backend.spec
#
# 주의: playwright(노드 드라이버), kiwipiepy(형태소 모델), google-genai 등은
#       데이터/네이티브 파일이 있어 collect_all 로 모두 포함해야 한다.

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("playwright", "kiwipiepy", "kiwipiepy_model", "google", "browser_cookie3"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    "aiosqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

block_cipher = None

a = Analysis(
    ["server_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-crawler-backend",
    console=True,          # 백그라운드 콘솔(로그 확인용). 배포 시 False 가능
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ai-crawler-backend",
)
