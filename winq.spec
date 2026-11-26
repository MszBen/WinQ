# PyInstaller build for WinQ (one-folder, windowed).
#   .venv\Scripts\pyinstaller.exe winq.spec --noconfirm
# Output: dist\WinQ\WinQ.exe   (check it with:  dist\WinQ\WinQ.exe --smoke-test)
# The installer wraps this folder: see installer\winq.iss and tools\build_release.ps1.
# ruff: noqa
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

VERSION = re.search(
    r'__version__ = "([^"]+)"', Path("src/winq/__init__.py").read_text(encoding="utf-8")
).group(1)
_nums = tuple(int(n) for n in re.findall(r"\d+", VERSION)[:3]) + (0,)
_nums = (_nums + (0, 0, 0, 0))[:4]

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "WinQ"),
                        StringStruct("FileDescription", "WinQ - audio cue playback"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "WinQ"),
                        StringStruct(
                            "LegalCopyright",
                            "Copyright (c) 2026 Benedek Miko-Szabo and other contributors",
                        ),
                        StringStruct("OriginalFilename", "WinQ.exe"),
                        StringStruct("ProductName", "WinQ"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    ["src/winq/__main__.py"],
    pathex=["src"],
    binaries=collect_dynamic_libs("soxr"),
    datas=[("src/winq/ui/theme.qss", "winq/ui"), ("src/winq/ui/winq.svg", "winq/ui")],
    # soundfile / sounddevice ship their DLLs (libsndfile, PortAudio) as package data;
    # pyinstaller-hooks-contrib collects them.
    hiddenimports=[],
    excludes=[
        "tkinter", "matplotlib", "IPython", "pytest", "PIL", "pydoc_data",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
        "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    ],
    noarchive=False,
)

# Qt pieces a widgets-only, English-only app never loads (~45 MB): QML/Quick, PDF,
# the software OpenGL fallback, the virtual keyboard, TLS/network-info plugins, Qt's own
# translations, and image formats other than SVG/ICO/PNG (PNG is built in).
_DROP = re.compile(
    r"(Qt6(Qml|Quick|Pdf|VirtualKeyboard|OpenGL)\w*\.dll"
    r"|opengl32sw\.dll"
    r"|PySide6[\/]translations[\/]"
    r"|plugins[\/](tls|networkinformation|platforminputcontexts|generic)[\/]"
    r"|plugins[\/]imageformats[\/](?!q(svg|ico)\.dll))",
    re.IGNORECASE,
)
a.binaries = [b for b in a.binaries if not _DROP.search(b[0])]
# PortAudio: WinQ loads only the 64-bit DLL without ASIO (the ASIO SDK has its own
# licence terms, and WASAPI is the default output).
_PA = re.compile(r"portaudio-binaries[\\/](?!libportaudio64bit\.dll$|README)", re.IGNORECASE)
a.binaries = [b for b in a.binaries if not _PA.search(b[0])]
a.datas = [d for d in a.datas if not _PA.search(d[0])]
a.datas = [d for d in a.datas if not _DROP.search(d[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WinQ",
    console=False,
    upx=False,
    icon="assets/winq.ico",  # regenerate from winq.svg with: python tools/make_icon.py
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, name="WinQ", upx=False)

# Licence files go next to WinQ.exe (not inside _internal) so users can find them.
import shutil

_out = Path(DISTPATH) / "WinQ"
for _f in ("LICENSE.txt", "THIRD_PARTY_NOTICES.txt"):
    shutil.copy2(_f, _out / _f)
shutil.copytree("licenses", _out / "licenses", dirs_exist_ok=True)
