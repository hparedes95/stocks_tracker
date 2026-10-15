"""Local Windows DPAPI vault. Never included in market exports or source control."""
from __future__ import annotations

import ctypes
import json
import os
import tempfile
from ctypes import wintypes

from .config import project_root


def _path():
    return project_root() / 'data' / 'private' / 'snaptrade.dpapi'


def _crypt(data: bytes, *, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('El almacenamiento de claves de esta integración requiere Windows.')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Windows no pudo acceder a las claves. Vuelve a configurarlas en este usuario.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def exists():
    return _path().is_file()


def save(client_id, consumer_key):
    from ..providers.snaptrade import Client
    Client(client_id, consumer_key)  # validate without network
    encrypted = _crypt(json.dumps([client_id.strip(), consumer_key.strip()]).encode())
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.snaptrade-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load():
    path = _path()
    if not path.is_file():
        raise RuntimeError('Configura las claves en Mi cartera → Conexión SnapTrade.')
    try:
        values = json.loads(_crypt(path.read_bytes(), decrypt=True))
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError
        return tuple(values)
    except (ValueError, OSError):
        raise RuntimeError('No se pudieron leer las claves locales. Vuelve a configurarlas.') from None


def forget():
    _path().unlink(missing_ok=True)
