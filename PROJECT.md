# NuzlockeSolver — Project Structure

## Purpose
Pokemon Emerald Nuzlocke battle solver. Reads live GBA memory, simulates trainer battles via Pokemon Showdown IPC, and recommends optimal move/switch actions using Gen 3 AI flag logic and shallow Monte Carlo search.

## Key Modules

| File | Role |
|------|------|
| `game_loop.py` | Top-level event loop; reads GBA state, triggers analysis, injects buttons |
| `battle_mode.py` | Live battle orchestration; manages `ShallowSearchProcess` + `MatchupInfo` |
| `shallow_search.py` | 2-turn MCTS with stratified importance sampling; runs as a free-threaded subprocess server |
| `search_process.py` | `ShallowSearchProcess`: spawns and communicates with `shallow_search.py` subprocess |
| `battle_sim.py` | IPC state parsing, `PokemonState`/`BattleContext` construction, `simulate_turn` |
| `ai_flags.py` | Gen 3 Emerald trainer AI flags 0–7 move scoring; all data classes (`MoveInfo`, `PokemonState`, `BattleContext`) |
| `ai_switch.py` | Gen 3 Emerald switch-in selection (two-stage: type score + damage calc) |
| `gen3_data.py` | Loads `gen3_moves.json`; provides `get_move_info(move_id)` → `MoveInfo` |
| `MatchupInfo.py` | Orchestrates parallel matchup simulations; produces `PrunedMatchupInfo` for team analysis |
| `test2.py` | Team selection: validates teams, normalizes scores, brute-force searches best squad |
| `team_analyzer.py` | Builds variant options (items, TMs, berries) for each Pokemon per opponent |
| `variant_builder.py` | Constructs concrete move/item variant sets for team optimization |
| `battle_analysis.py` | Battle state evaluation and decision scoring |
| `battle_record.py` | Persists battle results to disk |
| `emerald_reader.py` | Reads Pokemon/move/item data from GBA memory via mGBA |
| `emulator_core.py` | Low-level mGBA process management |
| `NodeIPC.py` | Binary-framed JSON IPC over subprocess stdout/stdin (4-byte big-endian length header) |
| `config.py` | Runtime configuration: badge boosts, opponent items, available TMs/items, trainer name |
| `berry_logic.py` | Determines optimal cure berry per opponent from Lum Berry simulation data |
| `item_meta.py` | Static metadata: type-boost items, status-to-berry mapping |
| `gen3_charset.py` | GBA character encoding/decoding |

## IPC Protocol
Node.js subprocess (`Connection.js`) communicates via stdin/stdout with 4-byte big-endian length-prefixed JSON frames. The same framing is used between Python processes (`NodeIPC.py`, `search_process.py`).

## Free-Threaded Parallelism
`shallow_search.py` runs under `python3.14t` (PYTHON_GIL=0) for true multi-threaded parallelism. It is launched as a subprocess by `ShallowSearchProcess` in `search_process.py`.

## Data Flow
```
GBA memory → emerald_reader → battle_mode → ShallowSearchProcess
                                         ↓
                              shallow_search (subprocess)
                                         ↓
                              simulate_turn × N (parallel NodeIPC workers)
                                         ↓
                              best_action → battle_mode → button injection
```

## Design Decisions
- **Opponent AI uses ai_flags**: During search rollouts, the opponent's move is selected using the same `score_move` + `ai_flags` logic used by actual Emerald trainers, not random sampling.
- **Stratified sampling over uniform**: The `StratifiedSampler` tracks opponent move frequencies (Dirichlet posterior) and binary RNG outcomes (Beta posterior) to focus simulation budget on high-probability branches.
- **Separation of IPC and simulation logic**: `NodeIPC.py` handles only framing; `battle_sim.py` handles state parsing; `shallow_search.py` handles search strategy.
- **Type map is canonical in gen3_data.py**: All modules import `TYPE_MAP` from `gen3_data` rather than defining their own copies.
