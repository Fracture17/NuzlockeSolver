const { Dex } = require('@pkmn/sim');
const gen3Dex = Dex.forGen(3);
const examples = ['return', 'shadowball', 'swordsdance', 'quickattack',
                  'sleeppowder', 'toxic', 'thunderwave', 'confuseray',
                  'reflect', 'raindance', 'gigadrain', 'bind',
                  'focuspunch', 'protect', 'spikes', 'batonpass',
                  'dreameater', 'explosion', 'willowisp'];
examples.forEach(id => {
    const m = gen3Dex.moves.get(id);
    if (!m || !m.exists) { console.log(id + ': NOT FOUND'); return; }
    const out = {
        id: m.id, name: m.name, type: m.type, category: m.category,
        basePower: m.basePower, priority: m.priority,
        flags: m.flags,
        status: m.status || null,
        volatileStatus: m.volatileStatus || null,
        weather: m.weather || null,
        sideCondition: m.sideCondition || null,
        selfBoost: m.selfBoost || null,
        boosts: m.boosts || null,
        secondary: m.secondary ? {status: m.secondary.status, volatileStatus: m.secondary.volatileStatus, boosts: m.secondary.boosts, chance: m.secondary.chance} : null,
        drain: m.drain || null,
        ohko: m.ohko || false,
        selfdestruct: m.selfdestruct || null,
    };
    console.log(JSON.stringify(out));
});
