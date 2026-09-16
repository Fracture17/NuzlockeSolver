/**
 * Dump all Gen 3 move data to stdout as JSON.
 * Run: node extract_gen3_moves.js > gen3_moves.json
 */
const { Dex } = require('@pkmn/sim');
const dex = Dex.forGen(3);

const out = {};
for (const [id, move] of Object.entries(dex.data.Moves)) {
    // Skip moves not available in Gen 3
    if (move.isNonstandard) continue;
    if (move.gen && move.gen > 3) continue;
    if (!move.gen && move.num > 354) continue;  // Gen 3 cap ~354 moves

    out[id] = {
        id: id,
        name: move.name,
        num: move.num,
        type: move.type,
        category: move.category,
        basePower: move.basePower || 0,
        priority: move.priority || 0,
        accuracy: move.accuracy,
        flags: move.flags || {},
        // Status/effect fields
        status: move.status || null,
        volatileStatus: move.volatileStatus || null,
        boosts: move.boosts || null,
        weather: move.weather || null,
        sideCondition: move.sideCondition || null,
        pseudoWeather: move.pseudoWeather || null,
        drain: move.drain || null,
        recoil: move.recoil || null,
        ohko: move.ohko || false,
        selfdestruct: move.selfdestruct || null,
        forceSwitch: move.forceSwitch || false,
        selfSwitch: move.selfSwitch || null,
        heal: move.heal || null,
        // Secondary effects (for burn/para/etc. on damaging moves)
        critRatio: move.critRatio || 1,
        secondary: move.secondary ? {
            status: move.secondary.status || null,
            volatileStatus: move.secondary.volatileStatus || null,
            boosts: move.secondary.boosts || null,
        } : null,
    };
}

console.log(JSON.stringify(out, null, 2));
