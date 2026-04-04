"""
emulator_core.py

Shared mgba initialization helpers used by game_loop.py and battle_test.py.
"""

import os
import sys

from mgba._pylib import ffi
import mgba.core as mgba_core
import mgba.image
from mgba.vfs import open_path as vfs_open_path


_stdout_redirected = False


def init_emulator(rom_path: str, save_path: str):
    """Load ROM, attach battery save, create video buffer, reset core, silence mgba C logs.

    Returns:
        (core, image, ffi, width, height)
    """
    core = mgba_core.load_path(rom_path)
    if core is None:
        raise RuntimeError(f"Failed to load ROM: {rom_path}")

    save_vfile = vfs_open_path(save_path, "r+")
    if save_vfile is None:
        raise RuntimeError(f"Failed to open save file: {save_path}")
    core.load_save(save_vfile)

    width, height = core.desired_video_dimensions()
    image = mgba.image.Image(width, height)
    core.set_video_buffer(image)
    core.reset()

    # mgba's C library logs to stdout (fd 1). Save fd 1, redirect it to /dev/null
    # to silence all C printf output, then point sys.stdout at the saved fd so
    # Python print() calls still work.  Only do this once — on re-init (e.g. loop
    # runs) fd 1 is already /dev/null and sys.stdout already points to the terminal.
    global _stdout_redirected
    if not _stdout_redirected:
        _saved_stdout_fd = os.dup(1)
        _devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(_devnull, 1)
        os.close(_devnull)
        sys.stdout = os.fdopen(_saved_stdout_fd, 'w', buffering=1)
        _stdout_redirected = True

    return core, image, ffi, width, height


def load_savestate(core, path: str, emu_lock=None) -> bool:
    """Load a savestate from path into core.

    Args:
        core: mgba core object.
        path: Path to .state file.
        emu_lock: Optional threading.Lock to acquire during load_raw_state call.

    Returns:
        True on success, False on failure.
    """
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except FileNotFoundError:
        print(f'[emulator_core] State file not found: {path}')
        return False

    buf = ffi.new('unsigned char[]', data)
    if emu_lock is not None:
        with emu_lock:
            ok = core.load_raw_state(buf)
    else:
        ok = core.load_raw_state(buf)

    if ok:
        print(f'[emulator_core] State loaded: {path}')
    else:
        print(f'[emulator_core] Load state failed: {path}')
    return ok
