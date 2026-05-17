"""
battle_state_tracker.py

Reads live in-battle state from GBA RAM each frame and prints whenever anything changes.
All addresses are vanilla pokeemerald US v1.0; RnB subtracts _BATTLE_ADDR_OFFSET = 0xC24.

BattlePokemon struct (0x58 bytes, gBattleMons[4]):
  0x00  u16  species
  0x02  u16  attack  (current in-battle stat)
  0x04  u16  defense
  0x06  u16  speed
  0x08  u16  spAttack
  0x0A  u16  spDefense
  0x0C  u16[4]  moves
  0x14  u32  IV bitfield + egg + ability bit
  0x18  s8[8]  statStages  [0=HP(unused), 1=Atk, 2=Def, 3=Spe, 4=SpAtk, 5=SpDef, 6=Acc, 7=Eva]
               value 6 = neutral; 0 = −6; 12 = +6
  0x20  u8   ability
  0x21  u8[2] types
  0x24  u8[4] pp
  0x28  u16  hp
  0x2A  u8   level
  0x2C  u16  maxHP
  0x2E  u16  item
  0x30  u8[11] nickname
  0x3C  u8[8]  otName
  0x44  u32  experience
  0x48  u32  personality
  0x4C  u32  status1  (non-volatile)
  0x50  u32  status2  (volatile)
  0x54  u32  otId
"""

from dataclasses import dataclass, field
from config import GAME_MODE

_BATTLE_ADDR_OFFSET = 0xC24 if GAME_MODE == 'rnb' else 0

# ─── Global battle variable addresses (vanilla pokeemerald.sym) ───────────────
BATTLE_MONS_ADDR     = 0x02024084 - _BATTLE_ADDR_OFFSET  # BattlePokemon[4]
BATTLE_MON_SIZE      = 0x58

STATUSES3_ADDR       = 0x020242AC - _BATTLE_ADDR_OFFSET  # u32[4]
DISABLE_STRUCTS_ADDR = 0x020242BC - _BATTLE_ADDR_OFFSET  # DisableStruct[4]
DISABLE_STRUCT_SIZE  = 0x1C

SIDE_STATUSES_ADDR   = 0x0202428E - _BATTLE_ADDR_OFFSET  # u16[2]
SIDE_TIMERS_ADDR     = 0x02024294 - _BATTLE_ADDR_OFFSET  # SideTimer[2]
SIDE_TIMER_SIZE      = 12                                 # 11 fields + 1 pad byte

WEATHER_ADDR         = 0x020243CC - _BATTLE_ADDR_OFFSET  # u16

# ─── BattlePokemon offsets ────────────────────────────────────────────────────
MON_SPECIES_OFF     = 0x00  # u16
MON_STATSTAGES_OFF  = 0x18  # s8[8]; index 1-7 = Atk,Def,Spe,SpAtk,SpDef,Acc,Eva
MON_HP_OFF          = 0x28  # u16
MON_LEVEL_OFF       = 0x2A  # u8
MON_MAXHP_OFF       = 0x2C  # u16
MON_STATUS1_OFF     = 0x4C  # u32 — non-volatile status
MON_STATUS2_OFF     = 0x50  # u32 — volatile status

# ─── STATUS1 bitmasks (pokeemerald constants/battle.h) ───────────────────────
S1_SLEEP         = 0x00000007   # bits 0-2: sleep counter 1-7
S1_POISON        = 0x00000008
S1_BURN          = 0x00000010
S1_FREEZE        = 0x00000020
S1_PARALYSIS     = 0x00000040
S1_TOXIC         = 0x00000080
S1_TOXIC_COUNTER = 0x00000F00   # bits 8-11: toxic turn counter 1-15

# ─── STATUS2 bitmasks (pokeemerald constants/battle.h) ───────────────────────
# NOTE: emerald_reader.py uses STATUS2_CONFUSION=0x1C and STATUS2_CURSED=0x2000,
# which do NOT match vanilla pokeemerald (correct: 0x7 and 0x10000000).
# These vanilla values are used here; verify against RnB source if tracking seems off.
S2_CONFUSION     = 0x00000007   # bits 0-2: confusion counter 1-7
S2_FLINCHED      = 0x00000008   # bit 3 (cleared each turn start)
S2_UPROAR        = 0x00000070   # bits 4-6: uproar counter
S2_BIDE          = 0x00000300   # bits 8-9: bide turn counter
S2_LOCK_CONFUSE  = 0x00000C00   # bits 10-11: Thrash/Outrage rampage counter
S2_MULTITURNS    = 0x00001000   # bit 12: multi-turn move active
S2_WRAPPED       = 0x0000E000   # bits 13-15: partial-trap counter
S2_INFATUATION   = 0x000F0000   # bits 16-19: one bit per battler
S2_FOCUS_ENERGY  = 0x00100000   # bit 20
S2_TRANSFORMED   = 0x00200000   # bit 21
S2_RECHARGE      = 0x00400000   # bit 22: must recharge (Hyper Beam etc.)
S2_RAGE          = 0x00800000   # bit 23
S2_SUBSTITUTE    = 0x01000000   # bit 24
S2_DESTINY_BOND  = 0x02000000   # bit 25
S2_ESCAPE_PREV   = 0x04000000   # bit 26: Mean Look / Spider Web
S2_NIGHTMARE     = 0x08000000   # bit 27
S2_CURSED        = 0x10000000   # bit 28
S2_FORESIGHT     = 0x20000000   # bit 29
S2_DEFENSE_CURL  = 0x40000000   # bit 30
S2_TORMENT       = 0x80000000   # bit 31

# ─── STATUS3 bitmasks (gStatuses3, pokeemerald constants/battle.h) ────────────
S3_LEECHSEED_SRC = 0x00000003   # bits 0-1: which battler seeded
S3_LEECHSEED     = 0x00000004   # bit 2
S3_ALWAYS_HITS   = 0x00000018   # bits 3-4: Lock-On / Mind Reader timer
S3_PERISH_SONG   = 0x00000020   # bit 5
S3_ON_AIR        = 0x00000040   # bit 6: Fly semi-invulnerable
S3_UNDERGROUND   = 0x00000080   # bit 7: Dig semi-invulnerable
S3_MINIMIZED     = 0x00000100   # bit 8
S3_CHARGED_UP    = 0x00000200   # bit 9: Charge
S3_ROOTED        = 0x00000400   # bit 10: Ingrain
S3_YAWN          = 0x00001800   # bits 11-12: Yawn timer (>0 = pending sleep)
S3_IMPRISONED    = 0x00002000   # bit 13
S3_GRUDGE        = 0x00004000   # bit 14
S3_NO_CRIT       = 0x00008000   # bit 15: Lucky Chant / can't crit
S3_MUD_SPORT     = 0x00010000   # bit 16
S3_WATER_SPORT   = 0x00020000   # bit 17
S3_UNDERWATER    = 0x00040000   # bit 18: Dive semi-invulnerable

# ─── Side condition bitmasks (gSideStatuses, pokeemerald constants/battle.h) ──
SIDE_REFLECT      = 0x0001
SIDE_LIGHTSCREEN  = 0x0002
SIDE_SPIKES       = 0x0010
SIDE_SAFEGUARD    = 0x0020
SIDE_FUTUREATTACK = 0x0040   # Future Sight / Doom Desire pending
SIDE_MIST         = 0x0100

# ─── Weather flags (gBattleWeather, pokeemerald constants/battle.h) ───────────
WX_RAIN_TEMP  = 0x0001
WX_RAIN_PERM  = 0x0004
WX_RAIN       = 0x0007   # temp | downpour(unused) | perm
WX_SAND_TEMP  = 0x0008
WX_SAND_PERM  = 0x0010
WX_SAND       = 0x0018
WX_SUN_TEMP   = 0x0020
WX_SUN_PERM   = 0x0040
WX_SUN        = 0x0060
WX_HAIL       = 0x0080

# ─── SideTimer struct offsets (within each 12-byte block) ────────────────────
ST_REFLECT_TIMER     = 0x00  # u8
ST_LIGHTSCREEN_TIMER = 0x02  # u8
ST_MIST_TIMER        = 0x04  # u8
ST_SAFEGUARD_TIMER   = 0x06  # u8
ST_FOLLOWME_TIMER    = 0x08  # u8
ST_SPIKES_AMOUNT     = 0x0A  # u8 (0-3)

# ─── DisableStruct offsets ────────────────────────────────────────────────────
DS_DISABLED_MOVE  = 0x04  # u16
DS_ENCORED_MOVE   = 0x06  # u16
DS_PROTECT_USES   = 0x08  # u8: consecutive Protect/Detect counter
DS_STOCKPILE      = 0x09  # u8: Stockpile counter (0-3)
DS_SUBSTITUTE_HP  = 0x0A  # u8: Substitute HP (0 = no sub)
DS_DISABLE_TIMER  = 0x0B  # u8: low nibble = disable timer (0 = not disabled)
DS_ENCORE_POS     = 0x0C  # u8: encored move slot index
DS_ENCORE_TIMER   = 0x0E  # u8: low nibble = encore timer
DS_PERISH_TIMER   = 0x0F  # u8: low nibble = perish song countdown (4→0; faint at 0)
DS_FURY_CUTTER    = 0x10  # u8: Fury Cutter consecutive hit counter
DS_ROLLOUT_TIMER  = 0x11  # u8: low nibble = Rollout / Ice Ball timer
DS_CHARGE_TIMER   = 0x12  # u8: low nibble = Solar Beam etc. charge timer
DS_TAUNT_TIMER    = 0x13  # u8: low nibble = Taunt timer
DS_IS_FIRST_TURN  = 0x16  # u8: 1 on the turn Pokemon was sent out
DS_TRUANT         = 0x18  # u8: bit 0 = truant loafing this turn

# ─── Stat stage labels (indices into statStages[8]) ──────────────────────────
_STAGE_LABELS = {1: 'Atk', 2: 'Def', 3: 'Spe', 4: 'SpA', 5: 'SpD', 6: 'Acc', 7: 'Eva'}


# ─── Snapshot dataclasses ─────────────────────────────────────────────────────

@dataclass
class BattleMonSnapshot:
    species: int = 0
    hp: int = 0
    maxhp: int = 0
    level: int = 0
    status1: int = 0
    status2: int = 0
    status3: int = 0
    stat_stages: tuple = (6, 6, 6, 6, 6, 6, 6, 6)  # indices 0-7
    sub_hp: int = 0
    protect_uses: int = 0
    stockpile: int = 0
    disable_timer: int = 0
    disabled_move: int = 0
    encore_timer: int = 0
    encored_move: int = 0
    perish_timer: int = 0
    taunt_timer: int = 0
    fury_cutter: int = 0
    rollout_timer: int = 0
    charge_timer: int = 0
    is_first_turn: int = 0


@dataclass
class BattleSideSnapshot:
    conditions: int = 0
    reflect_timer: int = 0
    lightscreen_timer: int = 0
    mist_timer: int = 0
    safeguard_timer: int = 0
    spikes_amount: int = 0


@dataclass
class BattleSnapshot:
    valid: bool = False
    mons: tuple = field(default_factory=lambda: (BattleMonSnapshot(), BattleMonSnapshot()))
    sides: tuple = field(default_factory=lambda: (BattleSideSnapshot(), BattleSideSnapshot()))
    weather: int = 0


# ─── Memory read helpers ──────────────────────────────────────────────────────

def _u8(core, addr: int) -> int:
    return int(core.memory.u8[addr])

def _u16(core, addr: int) -> int:
    return int(core.memory.u16[addr])

def _u32(core, addr: int) -> int:
    return int(core.memory.u32[addr])


# ─── Snapshot readers ────────────────────────────────────────────────────────

def _read_mon(core, battler_idx: int, status3: int) -> BattleMonSnapshot:
    mon_base = BATTLE_MONS_ADDR + battler_idx * BATTLE_MON_SIZE
    ds_base  = DISABLE_STRUCTS_ADDR + battler_idx * DISABLE_STRUCT_SIZE

    stages = tuple(_u8(core, mon_base + MON_STATSTAGES_OFF + i) for i in range(8))

    return BattleMonSnapshot(
        species      = _u16(core, mon_base + MON_SPECIES_OFF),
        hp           = _u16(core, mon_base + MON_HP_OFF),
        maxhp        = _u16(core, mon_base + MON_MAXHP_OFF),
        level        = _u8 (core, mon_base + MON_LEVEL_OFF),
        status1      = _u32(core, mon_base + MON_STATUS1_OFF),
        status2      = _u32(core, mon_base + MON_STATUS2_OFF),
        status3      = status3,
        stat_stages  = stages,
        sub_hp       = _u8 (core, ds_base + DS_SUBSTITUTE_HP),
        protect_uses = _u8 (core, ds_base + DS_PROTECT_USES),
        stockpile    = _u8 (core, ds_base + DS_STOCKPILE),
        disable_timer= _u8 (core, ds_base + DS_DISABLE_TIMER) & 0xF,
        disabled_move= _u16(core, ds_base + DS_DISABLED_MOVE),
        encore_timer = _u8 (core, ds_base + DS_ENCORE_TIMER) & 0xF,
        encored_move = _u16(core, ds_base + DS_ENCORED_MOVE),
        perish_timer = _u8 (core, ds_base + DS_PERISH_TIMER) & 0xF,
        taunt_timer  = _u8 (core, ds_base + DS_TAUNT_TIMER) & 0xF,
        fury_cutter  = _u8 (core, ds_base + DS_FURY_CUTTER),
        rollout_timer= _u8 (core, ds_base + DS_ROLLOUT_TIMER) & 0xF,
        charge_timer = _u8 (core, ds_base + DS_CHARGE_TIMER) & 0xF,
        is_first_turn= _u8 (core, ds_base + DS_IS_FIRST_TURN),
    )


def _read_side(core, side_idx: int) -> BattleSideSnapshot:
    timer_base = SIDE_TIMERS_ADDR + side_idx * SIDE_TIMER_SIZE
    return BattleSideSnapshot(
        conditions        = _u16(core, SIDE_STATUSES_ADDR + side_idx * 2),
        reflect_timer     = _u8(core, timer_base + ST_REFLECT_TIMER),
        lightscreen_timer = _u8(core, timer_base + ST_LIGHTSCREEN_TIMER),
        mist_timer        = _u8(core, timer_base + ST_MIST_TIMER),
        safeguard_timer   = _u8(core, timer_base + ST_SAFEGUARD_TIMER),
        spikes_amount     = _u8(core, timer_base + ST_SPIKES_AMOUNT),
    )


def read_battle_snapshot(core) -> BattleSnapshot:
    """Read a full in-battle state snapshot. Returns invalid snapshot on error."""
    try:
        statuses3 = [_u32(core, STATUSES3_ADDR + i * 4) for i in range(2)]
        return BattleSnapshot(
            valid   = True,
            mons    = tuple(_read_mon(core, i, statuses3[i]) for i in range(2)),
            sides   = tuple(_read_side(core, i) for i in range(2)),
            weather = _u16(core, WEATHER_ADDR),
        )
    except Exception:
        return BattleSnapshot(valid=False)


# ─── Human-readable helpers ───────────────────────────────────────────────────

def _species_name(sid: int, species_db: dict | None) -> str:
    if sid == 0:
        return '(none)'
    if species_db is not None:
        return species_db.get(str(sid), f'Species#{sid}')
    return f'Species#{sid}'


def _fmt_stage(raw: int) -> str:
    delta = raw - 6
    return f'+{delta}' if delta >= 0 else str(delta)


def _weather_name(w: int) -> str:
    if w & WX_RAIN: return 'Rain'
    if w & WX_SAND: return 'Sandstorm'
    if w & WX_SUN:  return 'Sun'
    if w & WX_HAIL: return 'Hail'
    return 'None'


def _status1_desc(s1: int) -> str:
    if s1 == 0:
        return 'none'
    parts = []
    sleep = s1 & S1_SLEEP
    if sleep:            parts.append(f'SLP({sleep})')
    if s1 & S1_TOXIC:    parts.append(f'TOX(turn {(s1 & S1_TOXIC_COUNTER) >> 8})')
    elif s1 & S1_POISON: parts.append('PSN')
    if s1 & S1_BURN:     parts.append('BRN')
    if s1 & S1_FREEZE:   parts.append('FRZ')
    if s1 & S1_PARALYSIS:parts.append('PAR')
    return ' '.join(parts)


def _s2_flags(s2: int) -> set:
    flags = set()
    c = s2 & S2_CONFUSION
    if c:                      flags.add(f'confused({c})')
    if s2 & S2_FLINCHED:       flags.add('flinched')
    u = (s2 & S2_UPROAR) >> 4
    if u:                      flags.add(f'uproar({u})')
    b = (s2 & S2_BIDE) >> 8
    if b:                      flags.add(f'bide({b})')
    lc = (s2 & S2_LOCK_CONFUSE) >> 10
    if lc:                     flags.add(f'rampage({lc})')
    wr = (s2 & S2_WRAPPED) >> 13
    if wr:                     flags.add(f'wrapped({wr})')
    if s2 & S2_INFATUATION:    flags.add('infatuated')
    if s2 & S2_FOCUS_ENERGY:   flags.add('focus_energy')
    if s2 & S2_TRANSFORMED:    flags.add('transformed')
    if s2 & S2_RECHARGE:       flags.add('recharging')
    if s2 & S2_RAGE:           flags.add('rage')
    if s2 & S2_SUBSTITUTE:     flags.add('substitute')
    if s2 & S2_DESTINY_BOND:   flags.add('destiny_bond')
    if s2 & S2_ESCAPE_PREV:    flags.add('escape_prevented')
    if s2 & S2_NIGHTMARE:      flags.add('nightmare')
    if s2 & S2_CURSED:         flags.add('cursed')
    if s2 & S2_FORESIGHT:      flags.add('foresight')
    if s2 & S2_DEFENSE_CURL:   flags.add('defense_curl')
    if s2 & S2_TORMENT:        flags.add('torment')
    return flags


def _s3_flags(s3: int) -> set:
    flags = set()
    if s3 & S3_LEECHSEED:   flags.add('leech_seed')
    ah = (s3 & S3_ALWAYS_HITS) >> 3
    if ah:                   flags.add(f'lock_on({ah})')
    if s3 & S3_PERISH_SONG:  flags.add('perish_song(s3)')
    if s3 & S3_ON_AIR:       flags.add('on_air')
    if s3 & S3_UNDERGROUND:  flags.add('underground')
    if s3 & S3_UNDERWATER:   flags.add('underwater')
    if s3 & S3_MINIMIZED:    flags.add('minimized')
    if s3 & S3_CHARGED_UP:   flags.add('charged')
    if s3 & S3_ROOTED:       flags.add('ingrain')
    yn = (s3 & S3_YAWN) >> 11
    if yn:                   flags.add(f'yawn({yn})')
    if s3 & S3_IMPRISONED:   flags.add('imprisoned')
    if s3 & S3_GRUDGE:       flags.add('grudge')
    if s3 & S3_NO_CRIT:      flags.add('no_crit')
    if s3 & S3_MUD_SPORT:    flags.add('mud_sport')
    if s3 & S3_WATER_SPORT:  flags.add('water_sport')
    return flags


# ─── Diff engine ─────────────────────────────────────────────────────────────

def diff_snapshots(
    prev: BattleSnapshot,
    curr: BattleSnapshot,
    species_db: dict | None = None,
) -> list[str]:
    """Return human-readable lines for every field that changed between snapshots."""
    if not curr.valid or not prev.valid:
        return []

    out = []
    labels = ['player', 'opp']

    for i, label in enumerate(labels):
        p, c = prev.mons[i], curr.mons[i]
        if c.species == 0:
            continue

        name = _species_name(c.species, species_db)
        pfx  = f'[{label} {name}]'

        # Switch detection
        if c.species != p.species and p.species != 0:
            old_name = _species_name(p.species, species_db)
            out.append(f'[{label}] switch: {old_name} → {name}')

        # HP
        if c.hp != p.hp:
            pct = f'{round(c.hp * 100 / c.maxhp)}%' if c.maxhp else '?%'
            out.append(f'{pfx} HP {p.hp} → {c.hp} ({pct})')

        # STATUS1 (non-volatile)
        if c.status1 != p.status1:
            out.append(f'{pfx} status: {_status1_desc(p.status1)} → {_status1_desc(c.status1)}')

        # STATUS2 (volatile flags)
        prev_s2 = _s2_flags(p.status2)
        curr_s2 = _s2_flags(c.status2)
        for flag in sorted(curr_s2 - prev_s2):
            out.append(f'{pfx} +{flag}')
        for flag in sorted(prev_s2 - curr_s2):
            out.append(f'{pfx} -{flag}')

        # STATUS3 (semi-invulnerable, leech seed, ingrain, yawn, etc.)
        prev_s3 = _s3_flags(p.status3)
        curr_s3 = _s3_flags(c.status3)
        for flag in sorted(curr_s3 - prev_s3):
            out.append(f'{pfx} +{flag}')
        for flag in sorted(prev_s3 - curr_s3):
            out.append(f'{pfx} -{flag}')

        # Stat stages (indices 1-7)
        for idx, stage_name in _STAGE_LABELS.items():
            pv, cv = p.stat_stages[idx], c.stat_stages[idx]
            if cv != pv:
                out.append(f'{pfx} {stage_name} stage: {_fmt_stage(pv)} → {_fmt_stage(cv)}')

        # Substitute HP
        if c.sub_hp != p.sub_hp:
            if p.sub_hp == 0:
                out.append(f'{pfx} substitute up ({c.sub_hp} HP)')
            elif c.sub_hp == 0:
                out.append(f'{pfx} substitute broken')
            else:
                out.append(f'{pfx} sub HP {p.sub_hp} → {c.sub_hp}')

        # Disable
        if c.disable_timer != p.disable_timer:
            if c.disable_timer > 0:
                out.append(f'{pfx} disabled move #{c.disabled_move} ({c.disable_timer} turns)')
            else:
                out.append(f'{pfx} disable ended')

        # Encore
        if c.encore_timer != p.encore_timer:
            if c.encore_timer > 0:
                out.append(f'{pfx} encored on slot {c.encored_move} ({c.encore_timer} turns)')
            else:
                out.append(f'{pfx} encore ended')

        # Taunt
        if c.taunt_timer != p.taunt_timer:
            if c.taunt_timer > 0:
                out.append(f'{pfx} taunted ({c.taunt_timer} turns)')
            else:
                out.append(f'{pfx} taunt ended')

        # Perish Song timer
        if c.perish_timer != p.perish_timer:
            if c.perish_timer > 0:
                out.append(f'{pfx} perish song countdown: {c.perish_timer}')
            else:
                out.append(f'{pfx} perish song expired')

        # Stockpile
        if c.stockpile != p.stockpile:
            out.append(f'{pfx} stockpile: {p.stockpile} → {c.stockpile}')

        # Protect consecutive counter
        if c.protect_uses != p.protect_uses:
            out.append(f'{pfx} protect_consecutive: {p.protect_uses} → {c.protect_uses}')

        # Fury Cutter
        if c.fury_cutter != p.fury_cutter:
            out.append(f'{pfx} fury_cutter streak: {p.fury_cutter} → {c.fury_cutter}')

        # Rollout/Ice Ball
        if c.rollout_timer != p.rollout_timer:
            if c.rollout_timer > 0:
                out.append(f'{pfx} rollout turn {c.rollout_timer}')
            else:
                out.append(f'{pfx} rollout ended')

        # Charge timer (Solar Beam etc.)
        if c.charge_timer != p.charge_timer:
            if c.charge_timer > 0:
                out.append(f'{pfx} charging (turn {c.charge_timer})')
            else:
                out.append(f'{pfx} charge complete')

        # First turn
        if c.is_first_turn != p.is_first_turn and c.is_first_turn:
            out.append(f'{pfx} first turn active')

    # Side conditions
    side_labels = ['player_side', 'opp_side']
    side_cond_flags = [
        (SIDE_REFLECT,      'reflect'),
        (SIDE_LIGHTSCREEN,  'light_screen'),
        (SIDE_SPIKES,       'spikes'),
        (SIDE_SAFEGUARD,    'safeguard'),
        (SIDE_FUTUREATTACK, 'future_attack'),
        (SIDE_MIST,         'mist'),
    ]
    for i, slbl in enumerate(side_labels):
        ps, cs = prev.sides[i], curr.sides[i]

        if cs.conditions != ps.conditions:
            for mask, cname in side_cond_flags:
                was = bool(ps.conditions & mask)
                now = bool(cs.conditions & mask)
                if was != now:
                    out.append(f'[{slbl}] {cname}: {"raised" if now else "ended"}')

        if cs.reflect_timer != ps.reflect_timer:
            out.append(f'[{slbl}] reflect timer: {cs.reflect_timer}')
        if cs.lightscreen_timer != ps.lightscreen_timer:
            out.append(f'[{slbl}] lightscreen timer: {cs.lightscreen_timer}')
        if cs.mist_timer != ps.mist_timer:
            out.append(f'[{slbl}] mist timer: {cs.mist_timer}')
        if cs.safeguard_timer != ps.safeguard_timer:
            out.append(f'[{slbl}] safeguard timer: {cs.safeguard_timer}')
        if cs.spikes_amount != ps.spikes_amount:
            out.append(f'[{slbl}] spikes: {ps.spikes_amount} → {cs.spikes_amount} layers')

    # Weather
    if curr.weather != prev.weather:
        out.append(f'[weather] {_weather_name(prev.weather)} → {_weather_name(curr.weather)}')

    return out
