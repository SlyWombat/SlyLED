# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('D:\\SlyLED\\desktop\\shared\\spa', 'spa'), ('D:\\SlyLED\\desktop\\shared\\parent_server.py', '.'), ('D:\\SlyLED\\desktop\\shared\\firmware_manager.py', '.'), ('D:\\SlyLED\\desktop\\shared\\spatial_engine.py', '.'), ('D:\\SlyLED\\desktop\\shared\\bake_engine.py', '.'), ('D:\\SlyLED\\desktop\\shared\\wled_bridge.py', '.'), ('D:\\SlyLED\\desktop\\shared\\dmx_profiles.py', '.'), ('D:\\SlyLED\\desktop\\shared\\dmx_artnet.py', '.'), ('D:\\SlyLED\\desktop\\shared\\dmx_sacn.py', '.'), ('D:\\SlyLED\\desktop\\shared\\show_generator.py', '.'), ('D:\\SlyLED\\desktop\\shared\\community_client.py', '.'), ('D:\\SlyLED\\desktop\\shared\\mover_control.py', '.'), ('D:\\SlyLED\\desktop\\shared\\space_mapper.py', '.'), ('D:\\SlyLED\\desktop\\shared\\surface_analyzer.py', '.'), ('D:\\SlyLED\\desktop\\shared\\aim', 'aim'), ('D:\\SlyLED\\desktop\\shared\\remote_orientation.py', '.'), ('D:\\SlyLED\\desktop\\shared\\dmx_universe.py', '.'), ('D:\\SlyLED\\desktop\\shared\\depth_runtime.py', '.'), ('D:\\SlyLED\\desktop\\shared\\depth_runner.py', '.'), ('D:\\SlyLED\\desktop\\shared\\camera_settings.py', '.'), ('D:\\SlyLED\\desktop\\shared\\ollama_runtime.py', '.'), ('D:\\SlyLED\\firmware\\registry.json', 'firmware'), ('D:\\SlyLED\\docs\\help', 'docs/help'), ('D:\\SlyLED\\docs\\build', 'docs/build'), ('D:\\SlyLED\\docs\\schema', 'docs/schema'), ('D:\\SlyLED\\docs\\USER_MANUAL.md', 'docs'), ('D:\\SlyLED\\docs\\USER_MANUAL_fr.md', 'docs'), ('D:\\SlyLED\\docs\\USER_MANUAL.docx', 'docs'), ('D:\\SlyLED\\docs\\USER_MANUAL_fr.docx', 'docs'), ('D:\\SlyLED\\docs\\USER_MANUAL.pdf', 'docs'), ('D:\\SlyLED\\docs\\USER_MANUAL_fr.pdf', 'docs')]
hiddenimports = ['pystray', 'paramiko', 'numpy', 'cv2', 'PIL._tkinter_finder']
datas += collect_data_files('esptool')
hiddenimports += collect_submodules('flask')
hiddenimports += collect_submodules('werkzeug')
hiddenimports += collect_submodules('esptool')
hiddenimports += collect_submodules('numpy')
hiddenimports += collect_submodules('cv2')


a = Analysis(
    ['D:\\SlyLED\\desktop\\shared\\main.py'],
    pathex=['D:\\SlyLED\\desktop\\shared'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    a.binaries,
    a.datas,
    [],
    name='SlyLED',
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
    version='D:\\SlyLED\\desktop\\windows\\version_info.txt',
    icon=['D:\\SlyLED\\images\\slyled.ico'],
)
