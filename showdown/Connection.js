// ipc.js
const fs = require('fs');

const { Dex, BattleStreams, RandomPlayerAI, Teams } = require('@pkmn/sim');
const { TeamGenerators } = require('@pkmn/randoms');
const { Battle } = require("@pkmn/sim");

// One-time setup per worker
Teams.setGeneratorFactory(TeamGenerators);
Dex.formats.get('gen3randombattle');
const spec = { formatid: 'gen3randombattle' };

const reader = process.stdin;
const writer = process.stdout;

process.stdin.resume();

function writeAsync(buf) {
	return new Promise((resolve, reject) => {
		const ok = writer.write(buf, err => err && reject(err));
		if (ok) resolve();
		else writer.once('drain', resolve);
	});
}

async function sendReply(obj) {
	const payload = Buffer.from(JSON.stringify(obj), 'utf8');
	const header = Buffer.allocUnsafe(4);
	header.writeUInt32BE(payload.length, 0);
	await writeAsync(Buffer.concat([header, payload]));
}

function calcMoveDamage(attacker, defender, moveId, battle, forceCrit = false) {
	const move = battle.dex.getActiveMove(moveId);

	move.willCrit = forceCrit;

	const origRandomizer = battle.randomizer.bind(battle);
	battle.randomizer = baseDamage => baseDamage;
	const damage = battle.actions.getDamage(attacker, defender, move, true);
	battle.randomizer = origRandomizer;
	return damage;
}

function userHandler(message) {
	/*const battleStream = new BattleStreams.BattleStream();
	const { p1, p2, omniscient: omni } = BattleStreams.getPlayerStreams(battleStream);

	omni.write(`>start ${JSON.stringify(spec)}\n`);
	omni.write(`>player p1 ${JSON.stringify({ name: 'Bot 1', team: Teams.pack(Teams.generate(spec.formatid)) })}\n`);
	omni.write(`>player p2 ${JSON.stringify({ name: 'Bot 2', team: Teams.pack(Teams.generate(spec.formatid)) })}\n`);

	return battleStream.battle.toJSON();*/


	//console.log(Teams.pack(Teams.generate(spec.formatid)));


	let battle = null;
	if ("new" in message) {
		//Start new battle
		const battleStream = new BattleStreams.BattleStream();
		const { p1, p2, omniscient: omni } = BattleStreams.getPlayerStreams(battleStream);

		omni.write(`>start ${JSON.stringify(spec)}\n`);
		omni.write(`>player p1 ${JSON.stringify({ name: 'Bot 1', team: message["team1"] })}\n`);
		omni.write(`>player p2 ${JSON.stringify({ name: 'Bot 2', team: message["team2"] })}\n`);

		battle = battleStream.battle;

		// Apply live HP / PP / status from GBA memory (F5 battle mode)
		const applyInitState = (side, initStates) => {
			if (!Array.isArray(initStates)) return;
			for (let i = 0; i < initStates.length && i < side.pokemon.length; i++) {
				const pkmn = side.pokemon[i];
				const init = initStates[i];
				if (typeof init.hp === 'number') {
					pkmn.hp = Math.max(0, init.hp);
					if (pkmn.hp === 0) pkmn.fainted = true;
				}
				if (Array.isArray(init.pp)) {
					for (let j = 0; j < init.pp.length && j < pkmn.moveSlots.length; j++) {
						pkmn.moveSlots[j].pp = init.pp[j];
					}
				}
				if (init.status) {
					pkmn.status = init.status;
					pkmn.statusData = { id: init.status };
				}
			}
		};
		applyInitState(battle.p1, message["p1InitState"]);
		applyInitState(battle.p2, message["p2InitState"]);

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
		battle.P1BadRNG = message["P1BadRNG"] ?? false;
		battle.P1GoodRNG = message["P1GoodRNG"] ?? false;
		battle.P2BadRNG = message["P2BadRNG"] ?? false;
		battle.P2GoodRNG = message["P2GoodRNG"] ?? false;
		battle.P1QuantizedRNG = message["P1QuantizedRNG"] ?? false;
		battle.P2QuantizedRNG = message["P2QuantizedRNG"] ?? false;
		battle.P1ForceCrit = message["P1ForceCrit"] ?? false;
		battle.P2ForceCrit = message["P2ForceCrit"] ?? false;
		battle.P1NoCrit = message["P1NoCrit"] ?? false;
		battle.P2NoCrit = message["P2NoCrit"] ?? false;
		battle.P1ForceHit = message["P1ForceHit"] ?? false;
		battle.P2ForceHit = message["P2ForceHit"] ?? false;
		battle.P1ForceMiss = message["P1ForceMiss"] ?? false;
		battle.P2ForceMiss = message["P2ForceMiss"] ?? false;
		battle.P1ForceEffect = message["P1ForceEffect"] ?? false;
		battle.P2ForceEffect = message["P2ForceEffect"] ?? false;
		battle.P1NoEffect = message["P1NoEffect"] ?? false;
		battle.P2NoEffect = message["P2NoEffect"] ?? false;

		battle.p1CritChance = null;
		battle.p2CritChance = null;
		battle.p1AccuracyChance = null;
		battle.p2AccuracyChance = null;
		battle.p1SecondaryChance = null;
		battle.p2SecondaryChance = null;
		battle.p1LumBlocked = null;
		battle.p2LumBlocked = null;

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
		const p1Locked = battle.p1.active[0].getLockedMove();
		if (p1Locked) {
			// Forced-action turn (recharge, Dig/Dive second turn, etc.):
			// only one action is legal and switching is not allowed.
			result["p1Moves"] = "0:";
		} else {
			let p1Moves = "";
			for(let m in battle.p1.active[0].moveSlots) {
				let pp = battle.p1.active[0].moveSlots[m].pp;
				let disabled = battle.p1.active[0].moveSlots[m].disabled;
				if(pp > 0 && !disabled) {
					p1Moves += m + ":";
				}
			}
			if (!p1Moves) p1Moves = "0:"; // struggle fallback
			result["p1Moves"] = p1Moves;
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
	}
	if(battle.p1.requestState === "switch") {
		//add viable switch-ins (faint-switch)
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
		const p2Locked = battle.p2.active[0].getLockedMove();
		if (p2Locked) {
			// Forced-action turn: only one action is legal, no switching.
			result["p2Moves"] = "0:";
		} else {
			let p2Moves = "";
			for(let m in battle.p2.active[0].moveSlots) {
				let pp = battle.p2.active[0].moveSlots[m].pp;
				let disabled = battle.p2.active[0].moveSlots[m].disabled;
				if(pp > 0 && !disabled) {
					p2Moves += m + ":";
				}
			}
			if (!p2Moves) p2Moves = "0:"; // struggle fallback
			result["p2Moves"] = p2Moves;
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
	}
	if(battle.p2.requestState === "switch") {
		//add viable switch-ins (faint-switch)
		let p2Switches = "";
		for(let p in battle.p2.pokemon) {
			let pokemon = battle.p2.pokemon[p];
			if(!pokemon.isActive && !pokemon.fainted) {
				p2Switches += p + ":";
			}
		}
		result["p2Switches"] = p2Switches;
	}

	// Move info: name, accuracy, secondary chance for each active pokemon's moves
	const getMoveInfo = (active) => active.moves.map(moveId => {
		const move = battle.dex.getActiveMove(moveId);
		const accuracy = move.accuracy === true ? null : move.accuracy;
		const secondaries = move.secondaries;
		const secondaryChance = (secondaries && secondaries.length > 0)
			? (secondaries[0].chance ?? 100)
			: null;
		return { name: move.name, accuracy, secondaryChance };
	});
	if (battle.p1.active[0] && battle.p1.active[0].hp) {
		result["p1MoveInfo"] = getMoveInfo(battle.p1.active[0]);
	}
	if (battle.p2.active[0] && battle.p2.active[0].hp) {
		result["p2MoveInfo"] = getMoveInfo(battle.p2.active[0]);
	}

	// Damage calculations: each active Pokemon's moves vs the opposing active
	if (battle.p1.active[0] && battle.p2.active[0] && battle.p1.active[0].hp && battle.p2.active[0].hp) {
		const p1Active = battle.p1.active[0];
		const p2Active = battle.p2.active[0];

		result["p1DmgCalcs"] = {};
		for (let i = 0; i < p1Active.moves.length; i++) {
			result["p1DmgCalcs"]["move " + (i + 1)] =
				calcMoveDamage(p1Active, p2Active, p1Active.moves[i], battle);
		}

		result["p2DmgCalcs"] = {};
		result["p2CritDmgCalcs"] = {};
		for (let i = 0; i < p2Active.moves.length; i++) {
			result["p2DmgCalcs"]["move " + (i + 1)] =
				calcMoveDamage(p2Active, p1Active, p2Active.moves[i], battle);
			result["p2CritDmgCalcs"]["move " + (i + 1)] =
				calcMoveDamage(p2Active, p1Active, p2Active.moves[i], battle, true);
		}

		const maxCritDmg = Math.max(...Object.values(result["p2CritDmgCalcs"]).map(v => v || 0));
		if (maxCritDmg > p1Active.hp) {
			result["p2CritDmgCalcsBench"] = {};
			for (let i = 0; i < battle.p1.pokemon.length; i++) {
				const bench = battle.p1.pokemon[i];
				if (!bench.isActive && !bench.fainted) {
					const benchCalcs = {};
					for (let j = 0; j < p2Active.moves.length; j++) {
						benchCalcs["move " + (j + 1)] =
							calcMoveDamage(p2Active, bench, p2Active.moves[j], battle, true);
					}
					result["p2CritDmgCalcsBench"][i] = benchCalcs;
				}
			}
		}
	}

	result["p1CritChance"] = battle.p1CritChance ?? null;
	result["p2CritChance"] = battle.p2CritChance ?? null;
	result["p1AccuracyChance"] = battle.p1AccuracyChance ?? null;
	result["p2AccuracyChance"] = battle.p2AccuracyChance ?? null;
	result["p1SecondaryChance"] = battle.p1SecondaryChance ?? null;
	result["p2SecondaryChance"] = battle.p2SecondaryChance ?? null;
	result["p1LumBlocked"] = battle.p1LumBlocked ?? null;
	result["p2LumBlocked"] = battle.p2LumBlocked ?? null;

	return result;
}





const v = userHandler({new: true});
userHandler({p1: "move 1", p2: "move 1", battle: v["battle"]});
userHandler({p1: "move 1", p2: "item superpotion", battle: v["battle"]});

let buffer = Buffer.alloc(0);

reader.on('data', async chunk => {
	buffer = Buffer.concat([buffer, chunk]);

	while (buffer.length >= 4) {
		const len = buffer.readUInt32BE(0);
		if (buffer.length < 4 + len) break;

		const payload = buffer.slice(4, 4 + len);
		buffer = buffer.slice(4 + len);

		let msg;
		try {
			msg = JSON.parse(payload.toString('utf8'));
		} catch (err) {
			await sendReply({ ok: false, error: "invalid_json" });
			continue;
		}

		try {
			const result = userHandler(msg);
			await sendReply({ ok: true, result });
		} catch (err) {
			await sendReply({ ok: false, error: String(err) });
		}
	}
});
