"""Tag .lnk files with the app's AppUserModelID so taskbar pinning works.

Windows resolves "Pin to taskbar" by copying the shortcut whose AUMID matches
the running window's AUMID (see vcplayer.app). Without one the pin degrades to
pythonw.exe's icon and drops launch arguments.
Usage: python scripts/set_aumid.py <shortcut.lnk> [<shortcut.lnk> ...]
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import uuid
from ctypes import POINTER, byref, c_void_p, wintypes
from pathlib import Path

try:
    from vcplayer.app import APP_USER_MODEL_ID
except ImportError:  # allow running without an installed package
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from vcplayer.app import APP_USER_MODEL_ID

logger = logging.getLogger(__name__)

HRESULT = ctypes.c_long
GPS_DEFAULT = 0
GPS_READWRITE = 2
VT_LPWSTR = 31
IID_IPROPERTY_STORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
PKEY_APP_USER_MODEL_ID_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"
PKEY_APP_USER_MODEL_ID_PID = 5


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, s: str) -> None:
        super().__init__()
        b = uuid.UUID(s).bytes_le
        self.Data1 = int.from_bytes(b[0:4], "little")
        self.Data2 = int.from_bytes(b[4:6], "little")
        self.Data3 = int.from_bytes(b[6:8], "little")
        for i in range(8):
            self.Data4[i] = b[8 + i]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


# PROPVARIANT layout is identical on x86/x64/ARM64 Windows: 4 USHORTs then an
# 8-byte-aligned union (16 bytes on 64-bit).
class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("pwszVal", ctypes.c_wchar_p),
    ]


class IPropertyStoreVtbl(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))),
        ("AddRef", ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)),
        ("Release", ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)),
        ("GetCount", ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(ctypes.c_uint32))),
        ("GetAt", ctypes.WINFUNCTYPE(HRESULT, c_void_p, ctypes.c_uint32, POINTER(PROPERTYKEY))),
        (
            "GetValue",
            ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT)),
        ),
        (
            "SetValue",
            ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT)),
        ),
        ("Commit", ctypes.WINFUNCTYPE(HRESULT, c_void_p)),
    ]


class IPropertyStore(ctypes.Structure):
    _fields_ = [("lpVtbl", POINTER(IPropertyStoreVtbl))]


def _property_key() -> PROPERTYKEY:
    key = PROPERTYKEY()
    key.fmtid = GUID(PKEY_APP_USER_MODEL_ID_FMTID)
    key.pid = PKEY_APP_USER_MODEL_ID_PID
    return key


def _open_store(lnk_path: str, flags: int) -> POINTER(IPropertyStore):
    shell32 = ctypes.windll.shell32
    fn = shell32.SHGetPropertyStoreFromParsingName
    fn.argtypes = [wintypes.LPCWSTR, c_void_p, ctypes.c_uint32, POINTER(GUID), POINTER(c_void_p)]
    fn.restype = HRESULT
    store_ptr = c_void_p()
    hr = fn(lnk_path, None, flags, byref(GUID(IID_IPROPERTY_STORE)), byref(store_ptr))
    if hr != 0:
        raise OSError(f"SHGetPropertyStoreFromParsingName({lnk_path}): hr=0x{hr & 0xFFFFFFFF:08X}")
    return ctypes.cast(store_ptr, POINTER(IPropertyStore))


def read_aumid(lnk_path: str) -> str | None:
    """Read System.AppUserModel.ID from a .lnk, or None if unset."""
    store = _open_store(lnk_path, GPS_DEFAULT)
    try:
        pv = PROPVARIANT()
        hr = store.contents.lpVtbl.contents.GetValue(store, byref(_property_key()), byref(pv))
        if hr != 0 or pv.vt != VT_LPWSTR or not pv.pwszVal:
            return None
        return pv.pwszVal
    finally:
        store.contents.lpVtbl.contents.Release(store)


def set_aumid(lnk_path: str) -> None:
    """Write the app AUMID into a .lnk, then verify by reading it back."""
    store = _open_store(lnk_path, GPS_READWRITE)
    try:
        pv = PROPVARIANT()
        pv.vt = VT_LPWSTR
        pv.pwszVal = APP_USER_MODEL_ID
        # SetValue deep-copies; caller keeps ownership of pv. Never call
        # PropVariantClear on it: pwszVal is not CoTaskMem-allocated memory.
        # Note: S_FALSE (1) is returned when the value is unchanged - still OK.
        hr = store.contents.lpVtbl.contents.SetValue(store, byref(_property_key()), byref(pv))
        if hr < 0:
            raise OSError(f"SetValue({lnk_path}): hr=0x{hr & 0xFFFFFFFF:08X}")
        hr = store.contents.lpVtbl.contents.Commit(store)
        if hr < 0:
            raise OSError(f"Commit({lnk_path}): hr=0x{hr & 0xFFFFFFFF:08X}")
    finally:
        store.contents.lpVtbl.contents.Release(store)
    actual = read_aumid(lnk_path)
    if actual != APP_USER_MODEL_ID:
        raise OSError(f"readback mismatch on {lnk_path}: {actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shortcuts", nargs="+", type=Path, help=".lnk files to tag")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ctypes.windll.ole32.CoInitialize(None)

    failures = 0
    for path in args.shortcuts:
        try:
            set_aumid(str(path))
        except OSError as exc:
            failures += 1
            logger.error("%s", exc)
        else:
            logger.info("OK: %s", path)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
