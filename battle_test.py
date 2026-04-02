"""
battle_test.py

Headless automated battle test runner.
Loads a savestate, runs run_battle_loop in a daemon thread, captures GBA battle
text each frame, cross-references events with recorded actions, and writes logs.

Usage:
    python3 battle_test.py [savestate_name]
    Default savestate: BrawlyTest
"""

import collections
import os
import sys
import threading
import time
from datetime import datetime

import pygame

from emulator_core import init_emulator, load_savestate
from gen3_charset import read_battle_text
from battle_record import BattleRecord
import battle_mode
from emerald_reader import LAST_MOVES_ADDR

# ─── Configuration ────────────────────────────────────────────────────────────
ROM_PATH    = "/home/Fracture/Downloads/emerald.gba"
SAVE_FILE   = "/home/Fracture/Downloads/emerald.sav"
NODE_SCRIPT = "/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js"

_HERE          = os.path.dirname(os.path.abspath(__file__))
_SAVESTATE_DIR = os.path.join(_HERE, 'savestates')
_LOG_DIR       = os.path.join(_HERE, 'test_logs')

BADGE_BOOST_ATK = True   # Stone Badge (Roxanne) — earned before Brawly
BADGE_BOOST_DEF = False
BADGE_BOOST_SPA = False
BADGE_BOOST_SPD = False
BADGE_BOOST_SPE = False

MCTS_ITERATIONS = 1000
NUM_WORKERS     = 15

_STUCK_TIMEOUT  = 30.0   # seconds of no output before declaring stuck
_MAX_MISMATCHES = 100    # faint mismatch count before declaring stuck
SCALE           = 4      # display scale factor (GBA native is 240×160)


class StuckError(RuntimeError):
    pass


class OutputCapture:
    """Wraps sys.stdout to capture each line into record.add_output while still printing."""

    def __init__(self, record: BattleRecord, original):
        self._record   = record
        self._original = original
        self._buf      = ''

    def write(self, text: str) -> int:
        self._original.write(text)
        self._buf += text
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            self._record.add_output(line)
        return len(text)

    def flush(self):
        self._original.flush()

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        return False


def run_battle_test(savestate_name: str, config: dict | None = None) -> BattleRecord:
    """Run a headless battle test against the given savestate.

    Args:
        savestate_name: Base name of the savestate file (without .state extension).
        config: Optional overrides: mcts_iterations, num_workers, test_actions.

    Returns:
        Completed BattleRecord with assertions run and logs saved.
    """
    cfg             = config or {}
    mcts_iterations = cfg.get('mcts_iterations', MCTS_ITERATIONS)
    num_workers     = cfg.get('num_workers', NUM_WORKERS)
    test_actions    = cfg.get('test_actions', None)

    savestate_path = os.path.join(_SAVESTATE_DIR, f'{savestate_name}.state')

    # ── Initialize emulator ──────────────────────────────────────────────────
    core, image, ffi, width, height = init_emulator(ROM_PATH, SAVE_FILE)
    if not load_savestate(core, savestate_path):
        raise FileNotFoundError(f'Savestate not found: {savestate_path}')

    # ── pygame display ────────────────────────────────────────────────────────
    pygame.init()
    screen = pygame.display.set_mode((width * SCALE, height * SCALE))
    pygame.display.set_caption(f'Battle Test — {savestate_name}')
    clock  = pygame.time.Clock()
    color_size = ffi.sizeof('mColor')

    # ── Shared state ──────────────────────────────────────────────────────────
    emu_lock         = threading.Lock()
    injected_keys    = collections.deque()
    opp_last_move_id = [0]
    record           = BattleRecord(savestate_name)

    # ── Wrap stdout for output capture ────────────────────────────────────────
    # sys.stdout at this point is already the fd-remapped file from init_emulator
    original_stdout = sys.stdout
    sys.stdout      = OutputCapture(record, original_stdout)

    # ── Start battle loop thread ─────────────────────────────────────────────
    battle_thread = threading.Thread(
        target=battle_mode.run_battle_loop,
        kwargs=dict(
            core=core,
            emu_lock=emu_lock,
            node_script_path=NODE_SCRIPT,
            injected_keys=injected_keys,
            opp_last_move_id=opp_last_move_id,
            badge_boosts={
                'atkBoost': BADGE_BOOST_ATK,
                'defBoost': BADGE_BOOST_DEF,
                'spaBoost': BADGE_BOOST_SPA,
                'spdBoost': BADGE_BOOST_SPD,
                'speBoost': BADGE_BOOST_SPE,
            },
            mcts_iterations=mcts_iterations,
            num_workers=num_workers,
            test_actions=test_actions,
            recorder=record,
        ),
        daemon=True,
    )
    record.last_output_time = time.monotonic()  # reset timer right before start
    battle_thread.start()

    # ── Frame loop ────────────────────────────────────────────────────────────
    # Debounce text: the GBA writes message bytes incrementally each frame.
    # Only record a string after it has been identical for TEXT_STABLE_FRAMES
    # consecutive frames, so we never capture a half-written message.
    TEXT_STABLE_FRAMES = 4
    last_recorded_text  = ''
    candidate_text      = ''
    candidate_frames    = 0

    try:
        while battle_thread.is_alive():
            # Process one frame worth of key injection
            if injected_keys:
                bitmask, frames = injected_keys[0]
                frames -= 1
                if frames <= 0:
                    injected_keys.popleft()
                else:
                    injected_keys[0] = (bitmask, frames)
                gba_keys = bitmask
            else:
                gba_keys = 0

            with emu_lock:
                core.set_keys(raw=gba_keys)
                core.run_frame()
                _opp_move = int(core.memory.u16[LAST_MOVES_ADDR + 2])
                if _opp_move:
                    opp_last_move_id[0] = _opp_move

            # Debounced GBA battle text capture
            text = read_battle_text(core)
            if text != candidate_text:
                candidate_text   = text
                candidate_frames = 1
            else:
                candidate_frames += 1
            if (candidate_frames == TEXT_STABLE_FRAMES
                    and candidate_text
                    and candidate_text != last_recorded_text):
                record.add_battle_text(candidate_text)
                last_recorded_text = candidate_text

            # Render frame to display
            raw     = bytes(ffi.buffer(image.buffer, width * height * color_size))
            surface = pygame.image.frombuffer(raw, (width, height), 'RGBX')
            scaled  = pygame.transform.scale(surface, (width * SCALE, height * SCALE))
            screen.blit(scaled, (0, 0))
            pygame.display.flip()
            pygame.event.pump()  # keep window responsive
            clock.tick(60)

            # Stuck detection
            elapsed = time.monotonic() - record.last_output_time
            if elapsed > _STUCK_TIMEOUT:
                pass
                #raise StuckError(
                #    f'No output for {elapsed:.1f}s — battle loop stuck')
            if record.mismatch_count > _MAX_MISMATCHES:
                raise StuckError(
                    f'Faint mismatch count {record.mismatch_count} exceeded limit')

    except StuckError as e:
        record.error = str(e)
        print(f'[battle_test] StuckError: {e}', file=original_stdout)
    finally:
        sys.stdout = original_stdout

    # ── Post-run ──────────────────────────────────────────────────────────────
    record.run_assertions()

    os.makedirs(_LOG_DIR, exist_ok=True)
    ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_base = os.path.join(_LOG_DIR, f'{savestate_name}_{ts}')
    record.save_log(log_base)

    return record


def main():
    savestate = sys.argv[1] if len(sys.argv) > 1 else 'BrawlyTest'
    print(f'[battle_test] Running test: {savestate}')
    record = run_battle_test(savestate)

    passed = sum(1 for a in record.assertions if a['passed'])
    failed = sum(1 for a in record.assertions if not a['passed'])
    print(f'\n[battle_test] Done. Outcome={record.outcome}  '
          f'Turns={len(record.turns)}  '
          f'Assertions: {passed} passed, {failed} failed')

    if record.error:
        print(f'[battle_test] Error: {record.error}')
        sys.exit(2)
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
