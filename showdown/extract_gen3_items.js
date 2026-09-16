/**
 * Dump Gen 3 item names to stdout as JSON.
 * Run: node extract_gen3_items.js > gen3_items.json
 *
 * Output format: { "0": "", "133": "Cheri Berry", "135": "Pecha Berry", "234": "Leftovers", ... }
 * Key 0 = no held item (empty string).
 *
 * GBA internal IDs match PS item.num for non-berry items (Leftovers=234, Choice Band=220, etc.).
 * For berries, GBA internal ID = PS item.num - 16 (e.g. GBA Pecha Berry=135, PS num=151).
 * Four-pass approach: standard berries take priority over non-berries at collision positions.
 */
const { Dex } = require('@pkmn/sim');
const dex = Dex.forGen(3);
const out = { '0': '' };

const isBerry = item => item.name.endsWith(' Berry');

// Pass 1: standard non-berry items (correct GBA IDs = PS nums for these)
for (const [id, item] of Object.entries(dex.data.Items)) {
    if (item.isNonstandard) continue;
    if (!item.num || item.num < 1) continue;
    if (item.gen && item.gen > 3) continue;
    if (isBerry(item)) continue;
    out[item.num] = item.name;
}

// Pass 2: standard berry items — GBA berry ID = PS num − 16 (overwrites non-berry collisions)
for (const [id, item] of Object.entries(dex.data.Items)) {
    if (item.isNonstandard) continue;
    if (!item.num || item.num < 1) continue;
    if (item.gen && item.gen > 3) continue;
    if (!isBerry(item)) continue;
    out[item.num - 16] = item.name;
}

// Pass 3: Past non-berry items (only if slot still empty)
for (const [id, item] of Object.entries(dex.data.Items)) {
    if (item.isNonstandard !== 'Past') continue;
    if (!item.num || item.num < 1) continue;
    if (item.gen && item.gen > 3) continue;
    if (isBerry(item)) continue;
    if (out[item.num] === undefined) out[item.num] = item.name;
}

// Pass 4: Past berry items (only if slot still empty — never overwrites Gen 3 berries)
for (const [id, item] of Object.entries(dex.data.Items)) {
    if (item.isNonstandard !== 'Past') continue;
    if (!item.num || item.num < 1) continue;
    if (item.gen && item.gen > 3) continue;
    if (!isBerry(item)) continue;
    const key = item.num - 16;
    if (out[key] === undefined) out[key] = item.name;
}

console.log(JSON.stringify(out, null, 2));
