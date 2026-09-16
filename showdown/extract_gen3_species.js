/**
 * Dump Gen 3 species names and per-ability-slot names to stdout as JSON.
 * Run: node extract_gen3_species.js > gen3_species.json
 *
 * Output format:
 *   gen3_species.json  →  { "1": "Bulbasaur", "2": "Ivysaur", ... }
 *   gen3_abilities.json (second arg) is written via redirect of second console.log
 *
 * Actually both outputs are printed sequentially separated by a delimiter.
 * Easier: run twice with --species / --abilities flag.
 *
 * Usage:
 *   node extract_gen3_species.js --species   > gen3_species.json
 *   node extract_gen3_species.js --abilities > gen3_abilities.json
 */
const { Dex } = require('@pkmn/sim');
const dex = Dex.forGen(3);

const mode = process.argv[2] || '--species';

const species = {};
const abilities = {};

for (const [id, s] of Object.entries(dex.data.Pokedex)) {
    if (s.isNonstandard) continue;
    if (!s.num || s.num < 1 || s.num > 386) continue;  // Gen 1-3 = up to #386
    // Skip alternate forms (Mega, Gmax, regional, etc.) — they share the base num
    if (s.baseSpecies && s.baseSpecies !== s.name) continue;

    species[s.num] = s.name;

    // abilities: {"0": "Overgrow", "1": "Chlorophyll", "H": "..."}
    // Gen 3 has no hidden abilities — drop "H"
    const ab = {};
    if (s.abilities['0']) ab['0'] = s.abilities['0'];
    if (s.abilities['1']) ab['1'] = s.abilities['1'];
    abilities[s.num] = ab;
}

if (mode === '--species') {
    console.log(JSON.stringify(species, null, 2));
} else if (mode === '--abilities') {
    console.log(JSON.stringify(abilities, null, 2));
} else {
    process.stderr.write(`Unknown mode: ${mode}\n`);
    process.exit(1);
}
