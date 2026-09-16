# Edit Tracking

## Architecture Overview

This project uses `Connection.js` as the IPC bridge between a Java/Python client and the `@pkmn/sim` battle simulator. The client sends JSON messages over stdin (length-prefixed), and receives JSON results over stdout.

### Key Files

- **Connection.js** — Main IPC server. Handles "new battle" and "apply choices" messages. Builds the result object returned to the client after each simulation step.
- **node_modules/@pkmn/sim/build/cjs/sim/battle-actions.js** — Core simulator battle logic. Custom battle-level fields (e.g. `p1CritChance`) are set here during simulation.
- **node_modules/@pkmn/sim/build/cjs/data/items.js** — Item definitions including event hooks. Modified to support Lum Berry status tracking.

### Battle Object Custom Fields

These fields are set on the `battle` object during simulation and read by `Connection.js` to populate the IPC result:

| Field | Set by | Purpose |
|---|---|---|
| `p1CritChance` / `p2CritChance` | `battle-actions.js` | Natural crit probability for the attacking side this turn |
| `p1AccuracyChance` / `p2AccuracyChance` | `battle-actions.js` | Move accuracy for the attacking side this turn |
| `p1SecondaryChance` / `p2SecondaryChance` | `battle-actions.js` | Secondary effect chance for the attacking side this turn |
| `p1LumBlocked` / `p2LumBlocked` | `items.js` (Lum Berry `onEat`) | Status condition id that was cured by Lum Berry this turn, or null |

### RNG Control Flags (set by client, read in Connection.js)

`forceAverageRandom`, `P1BadRNG`, `P1GoodRNG`, `P2BadRNG`, `P2GoodRNG`, `P1QuantizedRNG`, `P2QuantizedRNG`, `P1ForceCrit`, `P2ForceCrit`, `P1NoCrit`, `P2NoCrit`, `P1ForceHit`, `P2ForceHit`, `P1ForceMiss`, `P2ForceMiss`, `P1ForceEffect`, `P2ForceEffect`, `P1NoEffect`, `P2NoEffect`

### IPC Result Fields

- `battle` — serialized battle state (JSON)
- `p1Moves` / `p2Moves` — colon-separated move indices available this turn
- `p1Switches` / `p2Switches` — colon-separated switch slot indices available this turn
- `p1MoveInfo` / `p2MoveInfo` — array of `{name, accuracy, secondaryChance}` for active pokemon's moves
- `p1DmgCalcs` / `p2DmgCalcs` — damage calculations per move vs opposing active
- `p2CritDmgCalcs` — crit damage calculations for p2's moves vs p1 active
- `p2CritDmgCalcsBench` — crit damage calculations for p2's moves vs p1 bench (only if any crit move OHKOs p1 active)
- `p1CritChance` / `p2CritChance` / `p1AccuracyChance` / `p2AccuracyChance` / `p1SecondaryChance` / `p2SecondaryChance` — probability fields
- `p1LumBlocked` / `p2LumBlocked` — status id cured by Lum Berry this turn (e.g. `"psn"`, `"slp"`), or null

## Change Log

### Lum Berry status tracking
- **items.js** (`lumberry.onEat`): Before calling `cureStatus()`, captures `pokemon.status` (falling back to `'confusion'` if the pokemon has the confusion volatile) and writes it to `this.p1LumBlocked` or `this.p2LumBlocked` based on `pokemon.side.n`.
- **Connection.js**: Initializes `battle.p1LumBlocked = null` and `battle.p2LumBlocked = null` before choices are applied; reads them into `result["p1LumBlocked"]` and `result["p2LumBlocked"]` at the end.
