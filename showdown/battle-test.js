// battle-test.js
const { Dex, BattleStreams, RandomPlayerAI, Teams } = require('@pkmn/sim');
const { TeamGenerators } = require('@pkmn/randoms');
const { Battle } = require("@pkmn/sim");

// One-time setup per worker
Teams.setGeneratorFactory(TeamGenerators);
Dex.formats.get('gen3randombattle');
const spec = { formatid: 'gen3randombattle' };

function calcMoveDamage(attacker, defender, moveId, battle) {
	const move = battle.dex.getActiveMove(moveId);

	// Disable critical hits (per requirement)
	move.willCrit = false;

	// Temporarily replace the randomizer so getDamage returns the highest possible roll (100%)
	const origRandomizer = battle.randomizer.bind(battle);
	battle.randomizer = baseDamage => baseDamage;

	// Use the simulator's full damage pipeline; suppress battle log messages
	const damage = battle.actions.getDamage(attacker, defender, move, true);

	// Restore the original randomizer
	battle.randomizer = origRandomizer;

	return damage;
}

function userHandler(message) {
	let battle = null;
	if ("new" in message) {
		//Start new battle
		const battleStream = new BattleStreams.BattleStream();
		const { p1, p2, omniscient: omni } = BattleStreams.getPlayerStreams(battleStream);

		omni.write(`>start ${JSON.stringify(spec)}\n`);
		omni.write(`>player p1 ${JSON.stringify({ name: 'Bot 1', team: message["team1"] })}\n`);
		omni.write(`>player p2 ${JSON.stringify({ name: 'Bot 2', team: message["team2"] })}\n`);

		battle = battleStream.battle;

		battle.atkBoost = message["atkBoost"] ?? false;
		battle.defBoost = message["defBoost"] ?? false;
		battle.spaBoost = message["spaBoost"] ?? false;
		battle.spdBoost = message["spdBoost"] ?? false;
		battle.speBoost = message["speBoost"] ?? false;
	} else {
		//apply choices and simulate as far as possible
		battle = Battle.fromJSON(message["battle"]);
		battle.restart(() => { });

		battle.forceAverageRandom = message["forceAverageRandom"] ?? false;
		battle.forceBadRNG = message["forceBadRNG"] ?? false;

		//message should already have correct format; move {1-4} or switch {1-6}
		if("p1" in message) {
			battle.choose("p1", message["p1"]);
		}
		if("p2" in message) {
			battle.choose("p2", message["p2"]);
		}
	}

	let result = {};
	result["battle"] = battle.toJSON();

	if(battle.p1.requestState === "move") {
		//add moves
		let p1Moves = "";
		for(let m in battle.p1.active[0].moveSlots) {
			let pp = battle.p1.active[0].moveSlots[m].pp;
			let disabled = battle.p1.active[0].moveSlots[m].disabled;
			if(pp > 0 && !disabled) {
				p1Moves += m + ":";
			}

		}
		result["p1Moves"] = p1Moves;
	}
	if(battle.p1.requestState === "move" || battle.p1.requestState === "switch") {
		//add viable switch-ins
		let p1Switches = "";
		for(let p in battle.p1.pokemon) {
			let pokemon = battle.p1.pokemon[p];
			if(!pokemon.isActive && !pokemon.fainted) {
				p1Switches += p + ":";
			}
		}
		result["p1Switches"] = p1Switches;
	}

	if(battle.p2.requestState === "move") {
		//add moves
		let p2Moves = "";
		for(let m in battle.p2.active[0].moves) {
			let pp = battle.p2.active[0].moveSlots[m].pp;
			let disabled = battle.p2.active[0].moveSlots[m].disabled;
			if(pp > 0 && !disabled) {
				p2Moves += m + ":";
			}
		}
		result["p2Moves"] = p2Moves;
	}
	if(battle.p2.requestState === "move" || battle.p2.requestState === "switch") {
		//add viable switch-ins
		let p2Switches = "";
		for(let p in battle.p2.pokemon) {
			let pokemon = battle.p2.pokemon[p];
			if(!pokemon.isActive && !pokemon.fainted) {
				p2Switches += p + ":";
			}
		}
		result["p2Switches"] = p2Switches;
	}

	// Damage calculations: each active Pokemon's moves vs the opposing active
	if (battle.p1.active[0] && battle.p2.active[0]) {
		const p1Active = battle.p1.active[0];
		const p2Active = battle.p2.active[0];

		result["p1DmgCalcs"] = {};
		for (let i = 0; i < p1Active.moves.length; i++) {
			result["p1DmgCalcs"]["move " + (i + 1)] =
				calcMoveDamage(p1Active, p2Active, p1Active.moves[i], battle);
		}

		result["p2DmgCalcs"] = {};
		for (let i = 0; i < p2Active.moves.length; i++) {
			result["p2DmgCalcs"]["move " + (i + 1)] =
				calcMoveDamage(p2Active, p1Active, p2Active.moves[i], battle);
		}
	}

	return result;
}

// --- Test Setup ---

const team1 = Teams.pack([{
	name: 'Magikarp', species: 'Magikarp',
	item: 'Leftovers', ability: 'Swift Swim',
	moves: ['Hyper-Beam'], nature: 'Serious', level: 100,
	evs: {hp:0,atk:0,def:0,spa:0,spd:0,spe:0},
	ivs: {hp:31,atk:31,def:31,spa:31,spd:31,spe:31},
}]);

const team2 = Teams.pack([{
	name: 'Magikarp', species: 'Magikarp',
	item: '', ability: 'Swift Swim',
	moves: ['Splash'], nature: 'Serious', level: 100,
	evs: {hp:0,atk:0,def:0,spa:0,spd:0,spe:0},
	ivs: {hp:31,atk:31,def:31,spa:31,spd:31,spe:31},
}]);

// Initialize battle
let state = userHandler({atkBoost: false, new: true, team1, team2 });

// Set PP: team1 all moves get 1 PP, team2 Splash gets 40 PP
const initBattle = Battle.fromJSON(state.battle);
initBattle.restart(() => {});
for (const slot of initBattle.p1.active[0].moveSlots) slot.pp = 5;
initBattle.p2.active[0].moveSlots[0].pp = 40;
state = userHandler({ battle: initBattle.toJSON() });

function firstAvailableMove(p1Moves) {
	if (!p1Moves) return 'move 3';
	const idx = parseInt(p1Moves.split(':')[0], 10);
	return 'move ' + (idx + 1);
}

// Infinite loop
while (true) {
	//const p1Move = firstAvailableMove(state.p1Moves);
	const p1Move = "move 1"
	state = userHandler({ battle: state.battle, p1: p1Move, p2: 'move 1', forceBadRNG: false });
	//console.log(state);
	console.log(state.battle.sides[1].pokemon[0].maxHP);
	console.log(state.battle.sides[1].pokemon[0].hp);
	state = userHandler({ battle: state.battle, p1: p1Move, p2: 'item superpotion', forceBadRNG: true });
	//console.log(state);
	console.log(state.battle.sides[1].pokemon[0].maxHP);
	console.log(state.battle.sides[1].pokemon[0].hp);
	//break;
}
