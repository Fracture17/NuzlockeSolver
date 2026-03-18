"""
mgba_diag.py — diagnostic script to identify game_loop.py crash root cause.
Run with: python3 mgba_diag.py
"""
from mgba._pylib import ffi, lib
import mgba.core as mgba_core

ROM_PATH = "/home/Fracture/Downloads/1986 - Pokemon Emerald (U)(TrashMan).gba"

print("1. Testing lib.mCoreCreate(GBA) directly...")
native = lib.mCoreCreate(lib.mPLATFORM_GBA)
print(f"   pointer : {native}")
print(f"   is NULL : {native == ffi.NULL}")
if native != ffi.NULL:
    print(f"   init ptr: {native.init}")
    print(f"   deinit  : {native.deinit}")
    if native.deinit:
        native.deinit(native)

print()
print("2. Testing mgba_core.load_path()...")
try:
    core = mgba_core.load_path(ROM_PATH)
    print(f"   result  : {core}")
    print(f"   type    : {type(core)}")
except Exception as e:
    print(f"   EXCEPTION {type(e).__name__}: {e}")
