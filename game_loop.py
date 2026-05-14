"""
game_loop.py

Runs Pokémon Emerald in a pygame window via the mgba Python bindings.
The emulator runs at real-time 60fps in a background thread.
Keyboard and controller input is forwarded to the emulator each frame.
When a trainer battle starts, the opponent's team is read and logged automatically.

Dummy paths — set before running:
  ROM_PATH  : path to pokemon_emerald.gba
  SAVE_FILE : path to battery save (.sav) — any in-game save point works

Controls (keyboard defaults):
  Z          → A        X          → B
  Backspace  → Select   Return     → Start
  Arrow keys → D-pad    A          → L trigger
  S          → R trigger
"""

import collections
import os
import queue
import sys
import threading
import time

import numpy as np
import pygame
import sounddevice as sd
from mgba._pylib import ffi, lib
import mgba.core as mgba_core
import mgba.image
from mgba.vfs import open_path as vfs_open_path
import emulator_core

from emerald_reader import (
    ENEMY_PARTY_COUNT_ADDR,
    PLAYER_PARTY_ADDR,
    POKEMON_SIZE,
    SPECIES_JSON,
    MOVES_JSON,
    LAST_MOVES_ADDR,
    BASE_STATS_ROM,
    BASE_STATS_SIZE,
    EXP_GROUP_OFF,
    _min_exp_for_level,
    _read_bytes,
    decrypt_pokemon,
    internal_species_name,
    read_enemy_team,
    log_team,
    read_player_team,
    read_player_box,
    log_player_pokemon,
    dump_ram,
)

import json

import tkinter as tk
import tkinter.filedialog

import battle_mode

# ─── Configuration ────────────────────────────────────────────────────────────
from config import ROM_PATH, SAVE_FILE, NODE_SCRIPT
SCALE     = 6                                # 240×160 → 720×480
SCAN_INTERVAL = 3.0                          # seconds between RAM scans

_HERE             = os.path.dirname(os.path.abspath(__file__))
_SAVESTATE_DIR    = os.path.join(_HERE, 'savestates')
_SAVESTATE_CONFIG = os.path.join(_HERE, 'savestate_config.json')
_SAVESTATE_FILETYPES = [('Save states', '*.state'), ('All files', '*.*')]

SCAN_ADDR = 0x02024744   # gEnemyParty
MAX_VALID_SPECIES = 440   # species IDs above this after decryption are garbage
LEVEL_CAP = 55            # Party Pokémon are capped to this level every frame

# ─── Testing mode — bypass MCTS and use a fixed action sequence ───────────────
TESTING_MODE    = False
TEST_ACTIONS    = ['move 3']  # cycles if battle exceeds list length
# ─── Badge boosts — set True for each badge earned that boosts stats ──────────
from config import BADGE_BOOST_ATK, BADGE_BOOST_DEF, BADGE_BOOST_SP, BADGE_BOOST_SPE, TRAINER_NAME

# ─── Per-frame script constants ───────────────────────────────────────────────
_SAVE1_PTR    = 0x03005D8C   # IWRAM pointer to save block 1
_ITEMS_OFF    = 0x0560        # offset from save block base to items pocket
_ITEMS_SLOTS  = 30
_RARE_CANDY   = 68            # item ID 0x44

_LEVEL_OFFSET    = 0x54        # unencrypted level byte offset within party struct
# Growth substructure slot position for each personality % 24
_G_SLOT = [0, 0, 0, 0, 0, 0, 1, 1, 2, 3, 2, 3, 1, 1, 2, 3, 2, 3, 1, 1, 2, 3, 2, 3]


def _recalc_checksum(core, base: int) -> None:
    """Recompute and write the BoxPokemon checksum after modifying substructure data."""
    key = (int(core.memory.u32[base]) ^ int(core.memory.u32[base + 4])) & 0xFFFFFFFF
    total = 0
    for i in range(12):
        word = (int(core.memory.u32[base + 0x20 + i * 4]) ^ key) & 0xFFFFFFFF
        total += (word & 0xFFFF) + (word >> 16)
    core.memory.u16[base + 0x1C] = total & 0xFFFF


def _infinite_rare_candy(core) -> None:
    """Keep a Rare Candy in the items bag at quantity 99."""
    pocket = int(core.memory.u32[_SAVE1_PTR]) + _ITEMS_OFF
    for i in range(_ITEMS_SLOTS):
        addr    = pocket + i * 4
        item_id = int(core.memory.u16[addr])
        if item_id == _RARE_CANDY:
            if int(core.memory.u16[addr + 2]) < 99:
                core.memory.u16[addr + 2] = 99
            return
        elif item_id == 0:
            core.memory.u16[addr]     = _RARE_CANDY
            core.memory.u16[addr + 2] = 99
            return


def _cap_party_levels(core) -> None:
    """
    Per-frame level cap enforcement. For each party slot, caps both the unencrypted
    level byte and the encrypted EXP field in the Growth substructure, then
    recalculates the checksum if EXP was modified.
    """
    for i in range(6):
        base        = PLAYER_PARTY_ADDR + i * POKEMON_SIZE
        personality = int(core.memory.u32[base])
        otid        = int(core.memory.u32[base + 4])
        key         = (personality ^ otid) & 0xFFFFFFFF
        g_off       = base + 0x20 + _G_SLOT[personality % 24] * 12

        w0      = (int(core.memory.u32[g_off]) ^ key) & 0xFFFFFFFF
        species = w0 & 0xFFFF
        if not (1 <= species <= MAX_VALID_SPECIES):
            continue

        # Guard: verify existing checksum before modifying.  If the game wrote
        # new EXP data but hasn't yet updated the checksum (multi-frame update),
        # the checksum will be stale.  Skip this frame so we never write a
        # checksum that the game will immediately overwrite with a stale one.
        cs_total = 0
        for j in range(12):
            w = (int(core.memory.u32[base + 0x20 + j * 4]) ^ key) & 0xFFFFFFFF
            cs_total += (w & 0xFFFF) + (w >> 16)
        if (cs_total & 0xFFFF) != int(core.memory.u16[base + 0x1C]):
            continue  # mid-update or already corrupt — do not touch

        if int(core.memory.u8[base + _LEVEL_OFFSET]) > LEVEL_CAP:
            core.memory.u8[base + _LEVEL_OFFSET] = LEVEL_CAP

        exp_group = int(core.memory.u8[BASE_STATS_ROM + species * BASE_STATS_SIZE + EXP_GROUP_OFF])
        max_exp   = _min_exp_for_level(exp_group, LEVEL_CAP)
        exp       = (int(core.memory.u32[g_off + 4]) ^ key) & 0xFFFFFFFF
        if exp > max_exp:
            core.memory.u32[g_off + 4] = (max_exp ^ key) & 0xFFFFFFFF
            _recalc_checksum(core, base)



# ─── RAM scan helper ──────────────────────────────────────────────────────────

def _scan_party(core, addr, species_db, moves_db):
    """
    Read up to 6 Pokemon structs from addr, decrypt each, and return
    a list of human-readable strings for any slot with a valid species ID.
    """
    results = []
    for i in range(6):
        raw = _read_bytes(core, addr + i * POKEMON_SIZE, POKEMON_SIZE)
        try:
            p = decrypt_pokemon(raw)
        except Exception:
            continue
        sid = p['species']
        if sid == 0 or sid > MAX_VALID_SPECIES:
            continue
        name   = internal_species_name(sid, species_db)
        level  = p['level']
        chp    = p['current_hp']
        mhp    = p['max_hp']
        nature = p['nature']
        st     = p['stats']
        move_names = [
            moves_db.get(str(mid), f"Move#{mid}")
            for mid in p['moves'] if mid != 0
        ]
        moves_str = ', '.join(move_names) if move_names else '—'
        indent = '      '
        results.append(
            f"[{i+1}] Lv.{level:3d}  {name:<12}  HP {chp:3d}/{mhp:3d}  {nature}\n"
            f"{indent}    Atk {st['atk']:3d}  Def {st['def']:3d}  "
            f"SpA {st['spatk']:3d}  SpD {st['spdef']:3d}  Spe {st['spe']:3d}\n"
            f"{indent}    Moves: {moves_str}"
        )
    return results

# ─── GBA button bitmask ───────────────────────────────────────────────────────
# A=1  B=2  Select=4  Start=8  Right=16  Left=32  Up=64  Down=128  R=256  L=512
BTN_A      = 1
BTN_B      = 2
BTN_SELECT = 4
BTN_START  = 8
BTN_RIGHT  = 16
BTN_LEFT   = 32
BTN_UP     = 64
BTN_DOWN   = 128
BTN_R      = 256
BTN_L      = 512

# ─── Keyboard → GBA button mapping ───────────────────────────────────────────
KEY_MAP = {
    pygame.K_z:          BTN_A,
    pygame.K_x:          BTN_B,
    pygame.K_BACKSPACE:  BTN_SELECT,
    pygame.K_RETURN:     BTN_START,
    pygame.K_RIGHT:      BTN_RIGHT,
    pygame.K_LEFT:       BTN_LEFT,
    pygame.K_UP:         BTN_UP,
    pygame.K_DOWN:       BTN_DOWN,
    pygame.K_s:          BTN_R,
    pygame.K_a:          BTN_L,
}

# ─── Controller → GBA button mapping ─────────────────────────────────────────
# Adjust button indices to match your controller (use a joystick test tool to find them).
# Axis 0 = left stick X, Axis 1 = left stick Y (values -1.0 to 1.0).
JOYSTICK_BUTTON_MAP = {
    1:  BTN_A,
    0:  BTN_B,
    4:  BTN_SELECT,
    6:  BTN_START,
    9:  BTN_L,
    10: BTN_R,
}
# Controller speedup buttons (toggle; mutually exclusive)
JOY_BTN_SPEED_3X        = 13   # 3× speed cap
JOY_BTN_SPEED_UNLIMITED = 14   # unlimited speed
AXIS_DEADZONE  = 0.4   # ignore axis values smaller than this


def _joystick_bitmask(joystick: pygame.joystick.Joystick) -> int:
    """Read the current joystick state and return a GBA button bitmask."""
    mask = 0
    for btn_idx, gba_bit in JOYSTICK_BUTTON_MAP.items():
        if joystick.get_button(btn_idx):
            mask |= gba_bit
    # D-pad via hat (hat 0: (x, y) where 1=right/up, -1=left/down)
    if joystick.get_numhats() > 0:
        hx, hy = joystick.get_hat(0)
        if hx > 0:  mask |= BTN_RIGHT
        if hx < 0:  mask |= BTN_LEFT
        if hy > 0:  mask |= BTN_UP
        if hy < 0:  mask |= BTN_DOWN
    # D-pad via left stick (fallback)
    if joystick.get_numaxes() >= 2:
        ax = joystick.get_axis(0)
        ay = joystick.get_axis(1)
        if ax >  AXIS_DEADZONE: mask |= BTN_RIGHT
        if ax < -AXIS_DEADZONE: mask |= BTN_LEFT
        if ay < -AXIS_DEADZONE: mask |= BTN_UP
        if ay >  AXIS_DEADZONE: mask |= BTN_DOWN
    return mask


def _savestate_last_loaded() -> str | None:
    """Return path of last loaded savestate, or None if not set."""
    try:
        with open(_SAVESTATE_CONFIG) as f:
            return json.load(f).get('last_loaded')
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _savestate_set_last_loaded(path: str) -> None:
    with open(_SAVESTATE_CONFIG, 'w') as f:
        json.dump({'last_loaded': path}, f)


def _savestate_save(core, emu_lock) -> None:
    os.makedirs(_SAVESTATE_DIR, exist_ok=True)
    root = tk.Tk(); root.withdraw()
    path = tk.filedialog.asksaveasfilename(
        title='Save State',
        initialdir=_SAVESTATE_DIR,
        defaultextension='.state',
        filetypes=_SAVESTATE_FILETYPES,
        parent=root,
    )
    root.destroy()
    if not path:
        return
    with emu_lock:
        buf = core.save_raw_state()
    if buf is None:
        print('[game_loop] Save state failed (core returned None)')
        return
    with open(path, 'wb') as f:
        f.write(bytes(ffi.buffer(buf)))
    print(f'[game_loop] State saved: {path}')


def _savestate_load(core, emu_lock, path: str) -> bool:
    """Load savestate from path. Returns True on success."""
    return emulator_core.load_savestate(core, path, emu_lock)


def _savestate_load_browse(core, emu_lock) -> None:
    """Open file browser to select a savestate, load it, and persist the path."""
    os.makedirs(_SAVESTATE_DIR, exist_ok=True)
    root = tk.Tk(); root.withdraw()
    path = tk.filedialog.askopenfilename(
        title='Load State',
        initialdir=_SAVESTATE_DIR,
        filetypes=_SAVESTATE_FILETYPES,
        parent=root,
    )
    root.destroy()
    if not path:
        return
    if _savestate_load(core, emu_lock, path):
        _savestate_set_last_loaded(path)


def main():
    # ── Load lookup tables ──────────────────────────────────────────────────
    with open(SPECIES_JSON) as f: species_db = json.load(f)
    with open(MOVES_JSON)   as f: moves_db   = json.load(f)

    # ── Load emulator core ──────────────────────────────────────────────────
    core, image, ffi, width, height = emulator_core.init_emulator(ROM_PATH, SAVE_FILE)

    # ── Emulation thread ────────────────────────────────────────────────────
    emu_lock         = threading.Lock()
    running          = [True]   # list so the thread closure can read the flag
    injected_keys      = collections.deque()  # (bitmask, frames_remaining) pairs
    opp_last_move_id   = [0]  # last non-zero gLastMoves[1]; updated per-frame by emu_loop
    # Speed mode: 0 = normal (60 fps), 1 = 3× (180 fps cap), 2 = unlimited
    # TAB held forces unlimited temporarily regardless of mode.
    speed_mode         = [0]    # 0=normal, 1=3×, 2=unlimited (controller/F4 toggle)
    _tab_held          = [False] # TAB held → temporary unlimited without altering speed_mode
    _prev_btn13        = [False]
    _prev_btn14        = [False]
    _battle_loop_active = threading.Event()  # set while run_battle_loop thread is alive

    _out_rate = int(sd.query_devices(kind='output')['default_samplerate'])
    _gba_rate = 32768

    def _resample(chunk):
        """Linear-interpolation resample (N,2) int16 from GBA rate to device rate."""
        if _gba_rate == _out_rate or len(chunk) == 0:
            return chunk
        n_out = max(1, round(len(chunk) * _out_rate / _gba_rate))
        x_old = np.arange(len(chunk), dtype=np.float64)
        x_new = np.linspace(0.0, len(chunk) - 1, n_out)
        result = np.empty((n_out, 2), dtype=np.int16)
        for ch in range(2):
            result[:, ch] = np.clip(
                np.interp(x_new, x_old, chunk[:, ch].astype(np.float64)),
                -32768, 32767,
            ).astype(np.int16)
        return result

    audio_queue = queue.Queue(maxsize=6)
    _leftover   = [np.zeros((0, 2), dtype=np.int16)]

    def _audio_cb(outdata, frames, time_info, status):
        out = np.zeros((frames, 2), dtype=np.int16)
        pos = 0
        # consume leftover from previous callback first
        buf = _leftover[0]
        if len(buf):
            n = min(len(buf), frames)
            out[:n] = buf[:n]
            _leftover[0] = buf[n:]
            pos = n
        # pull fresh chunks from the queue
        while pos < frames:
            try:
                chunk = audio_queue.get_nowait()
                n = min(len(chunk), frames - pos)
                out[pos:pos + n] = chunk[:n]
                _leftover[0] = chunk[n:] if n < len(chunk) else np.zeros((0, 2), dtype=np.int16)
                pos += n
            except queue.Empty:
                break  # fill rest with silence
        outdata[:] = out

    def emu_loop():
        while running[0]:
            t = time.perf_counter()
            with emu_lock:
                core.run_frame()
                _cap_party_levels(core)
                _infinite_rare_candy(core)
                # gLastMoves[1] is valid during turn resolution; 0 at move-selection screen.
                # Store the last non-zero value so suggest_and_queue_move() can read it.
                _opp_move = int(core.memory.u16[LAST_MOVES_ADDR + 2])
                if _opp_move:
                    opp_last_move_id[0] = _opp_move
                # drain mGBA audio buffer every frame
                audio_buf = core._core.getAudioBuffer(core._core)
                available  = lib.mAudioBufferAvailable(audio_buf)
                if available > 0:
                    fast = speed_mode[0] != 0 or _tab_held[0]
                    if fast:
                        lib.mAudioBufferClear(audio_buf)
                    else:
                        raw = ffi.new(f'int16_t[{available * 2}]')
                        read = lib.mAudioBufferRead(audio_buf, raw, available)
                        if read > 0:
                            chunk = np.frombuffer(
                                ffi.buffer(raw, read * 2 * 2), dtype=np.int16
                            ).reshape(read, 2).copy()
                            chunk = _resample(chunk)
                            try:
                                audio_queue.put_nowait(chunk)
                            except queue.Full:
                                pass

            mode = 2 if _tab_held[0] else speed_mode[0]
            if mode == 0:
                frame_time = 1.0 / 60.0
            elif mode == 1:
                frame_time = 1.0 / 180.0
            else:
                frame_time = 0.0  # unlimited: no sleep
            elapsed = time.perf_counter() - t
            remaining = frame_time - elapsed
            if remaining > 0:
                time.sleep(remaining)

    # ── Audio stream (start before emu thread) ──────────────────────────────
    audio_stream = sd.OutputStream(
        samplerate=_out_rate, channels=2, dtype='int16',
        blocksize=512, callback=_audio_cb,
    )
    audio_stream.start()

    # ── pygame setup (before emu thread so SDL is fully init'd first) ──────
    pygame.init()
    screen = pygame.display.set_mode((width * SCALE, height * SCALE))
    pygame.display.set_caption("Pokémon Emerald")
    clock = pygame.time.Clock()

    joystick = None
    if pygame.joystick.get_count() > 0:
        joystick = pygame.joystick.Joystick(0)
        joystick.init()
        print(f"[game_loop] Controller detected: {joystick.get_name()}")
    else:
        print("[game_loop] No controller detected — using keyboard only")

    threading.Thread(target=emu_loop, daemon=True).start()

    # ── Battle detection state ───────────────────────────────────────────────
    last_enemy_count = 0

    # ── Main loop ───────────────────────────────────────────────────────────
    while running[0]:
        # Process events
        keys_held = pygame.key.get_pressed()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running[0] = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F4:
                speed_mode[0] = 0 if speed_mode[0] != 0 else 2
                print(f'[game_loop] F4 — speed mode {speed_mode[0]}')
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                _savestate_save(core, emu_lock)
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_l and (event.mod & pygame.KMOD_CTRL):
                _savestate_load_browse(core, emu_lock)
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F3:
                last = _savestate_last_loaded()
                if last:
                    _savestate_load(core, emu_lock, last)
                else:
                    print('[game_loop] F3 — no savestate loaded yet')
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F5:
                if _battle_loop_active.is_set():
                    print('[game_loop] F5 — battle loop already running, ignoring.')
                else:
                    print('[game_loop] F5 — starting autonomous battle loop...')
                    _battle_loop_active.set()
                    def _battle_loop_wrapper():
                        try:
                            battle_mode.run_battle_loop(
                                core, emu_lock, NODE_SCRIPT, injected_keys,
                                opp_last_move_id,
                                badge_boosts={
                                    'atkBoost': BADGE_BOOST_ATK,
                                    'defBoost': BADGE_BOOST_DEF,
                                    'spaBoost': BADGE_BOOST_SP,
                                    'spdBoost': BADGE_BOOST_SP,
                                    'speBoost': BADGE_BOOST_SPE,
                                },
                                test_actions=TEST_ACTIONS if TESTING_MODE else None,
                                trainer_name=TRAINER_NAME,
                            )
                        finally:
                            _battle_loop_active.clear()
                    threading.Thread(target=_battle_loop_wrapper, daemon=True).start()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F2:
                print("[game_loop] F2 pressed — reading opponent team...")
                with emu_lock:
                    team = read_enemy_team(core)
                log_team(team, trainer_name=TRAINER_NAME)
                if team:
                    print(f"[game_loop] Logged {len(team)} opponent Pokémon to file.")
                else:
                    print("[game_loop] No opponent team found (not in battle?).")
                print("[game_loop] F2 — reading player team and PC boxes...")
                with emu_lock:
                    player_team = read_player_team(core)
                    boxes = read_player_box(core)
                for p in player_team:
                    p['level'] = LEVEL_CAP
                for slot in boxes:
                    for p in slot:
                        p['level'] = LEVEL_CAP
                log_player_pokemon(player_team, boxes)

        # Build GBA input bitmask
        gba_keys = 0
        for key, bit in KEY_MAP.items():
            if keys_held[key]:
                gba_keys |= bit
        if joystick is not None:
            gba_keys |= _joystick_bitmask(joystick)
            n = joystick.get_numbuttons()

            btn13 = n > JOY_BTN_SPEED_3X        and joystick.get_button(JOY_BTN_SPEED_3X)
            btn14 = n > JOY_BTN_SPEED_UNLIMITED  and joystick.get_button(JOY_BTN_SPEED_UNLIMITED)

            if btn13 and not _prev_btn13[0]:
                speed_mode[0] = 0 if speed_mode[0] == 1 else 1
                print(f'[game_loop] Speed mode: {speed_mode[0]} (3× toggle)')
            elif btn14 and not _prev_btn14[0]:
                speed_mode[0] = 0 if speed_mode[0] == 2 else 2
                print(f'[game_loop] Speed mode: {speed_mode[0]} (unlimited toggle)')

            _prev_btn13[0] = btn13
            _prev_btn14[0] = btn14

        _tab_held[0] = bool(keys_held[pygame.K_TAB])

        # Inject queued keys if available, otherwise use physical input
        if injected_keys:
            bitmask, frames = injected_keys[0]
            gba_keys = bitmask
            frames -= 1
            if frames <= 0:
                injected_keys.popleft()
            else:
                injected_keys[0] = (bitmask, frames)

        with emu_lock:
            core.set_keys(raw=gba_keys)

        # Battle detection — triggered once when enemy count goes 0 → N
        with emu_lock:
            enemy_count = core.memory.u8[ENEMY_PARTY_COUNT_ADDR]
        if enemy_count > 0 and last_enemy_count == 0:
            print(f"[game_loop] Battle start detected — {enemy_count} opponent Pokémon")
            with emu_lock:
                team = read_enemy_team(core)
            log_team(team)
            with emu_lock:
                player_team = read_player_team(core)
                boxes = read_player_box(core)
            log_player_pokemon(player_team, boxes)
        last_enemy_count = enemy_count

        # Render current frame
        # Note: if colors look wrong (green tint etc.), change 'RGBA' to 'BGRA' below.
        color_size = ffi.sizeof('mColor')
        with emu_lock:
            raw = bytes(ffi.buffer(image.buffer, width * height * color_size))
        surface = pygame.image.frombuffer(raw, (width, height), 'RGBX')
        scaled  = pygame.transform.scale(surface, (width * SCALE, height * SCALE))
        screen.blit(scaled, (0, 0))
        pygame.display.flip()
        clock.tick(60)

    audio_stream.stop()
    audio_stream.close()
    pygame.quit()


if __name__ == "__main__":
    main()
