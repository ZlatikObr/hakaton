"""Apply no_new_privs after the host LSM transition, before importing the server.

The domain deployment uses this startup order with explicit owner authorization
for a host that rejects initial exec when Docker itself sets no_new_privs.
"""
import ctypes
import sys


def enforce_no_new_privileges():
    if sys.platform != 'linux':
        raise RuntimeError('Container startup requires Linux no_new_privs support.')
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(38, 1, 0, 0, 0) != 0 or prctl(39, 0, 0, 0, 0) != 1:
        raise RuntimeError('Container startup stopped: no_new_privs could not be enabled.')


def main():
    enforce_no_new_privileges()
    print('Startup protection: no_new_privs=1', flush=True)
    import uvicorn
    uvicorn.run('tz_audit.api:create_app', factory=True, host='0.0.0.0',
        port=8000, access_log=False)


if __name__ == '__main__':
    main()
