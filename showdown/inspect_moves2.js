const { Dex } = require('@pkmn/sim');
const gen3Dex = Dex.forGen(3);

// Check sound and high-crit flags
['hypervoice','boomburst','slash','stoneedge','nightslash','crosschop',
 'crabhammer','razorleaf','karatechop','aeroblast','skullbash',
 'solarbeam','fly','dig','bounce','dive',
 'roar','whirlwind','memento','helpinghand','batonpass',
 'perishsong','destinybond','meanlook','lockon','mindreader',
 'futuresight','doomdesire','encore','torment','attract',
 'stockpile','spitup','bellydrum','endure','substitute'].forEach(id => {
    const m = gen3Dex.moves.get(id);
    if (!m || !m.exists) return;
    const interesting = {};
    if (m.flags.sound) interesting.sound = true;
    if (m.flags.highCritRatio) interesting.highCritRatio = true;
    if (m.flags.drain) interesting.flagDrain = true;
    if (m.selfdestruct) interesting.selfdestruct = m.selfdestruct;
    if (m.volatileStatus) interesting.volatileStatus = m.volatileStatus;
    if (m.selfSwitch) interesting.selfSwitch = m.selfSwitch;
    if (m.forceSwitch) interesting.forceSwitch = m.forceSwitch;
    if (m.boosts) interesting.boosts = m.boosts;
    if (m.selfBoost) interesting.selfBoost = m.selfBoost;
    if (Object.keys(interesting).length) {
        console.log(id, JSON.stringify(interesting));
    }
});
