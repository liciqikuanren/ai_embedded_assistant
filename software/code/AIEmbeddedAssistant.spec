# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


def _safe_submodules(pkg):
    """收集 pyocd 子模块（未安装时静默跳过，不影响仅用 J-Link 的构建）。"""
    try:
        return collect_submodules(pkg)
    except Exception:
        return []


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('plugins', 'plugins'), ('core/assets', 'core/assets'), ('img', 'img')],
    hiddenimports=['plugins.recorder', 'plugins.serial_port', 'plugins.transmitter', 'plugins.history', 'plugins.justfloat', 'plugins.float_recorder', 'plugins.tcp_forward', 'plugins.rtt', 'plugins.logger', 'plugins.codec', 'plugins.commands', 'plugins.config', 'plugins.ai_server', 'serial', 'uvicorn', 'mcp.server.fastmcp', 'mcp.server.streamable_http', 'pydantic_settings', 'sse_starlette', 'pylink', 'elftools'] + _safe_submodules('pyocd'),
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
    name='AIEmbeddedAssistant',
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
    icon=['logo.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AIEmbeddedAssistant',
)
