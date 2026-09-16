// profile-test-parallel.js
const { Worker, isMainThread, parentPort, workerData, threadId } = require('worker_threads');
const { Battle } = require("@pkmn/sim");

const NUM_THREADS = 1;

if (isMainThread) {
	// Main thread: spawn workers
	// eslint-disable-next-line radix
	const battlesPerThread = parseInt(process.argv[2], 10) || 100;
	console.log(`Starting ${NUM_THREADS} threads, each simulating ${battlesPerThread} battles...`);
	const startTime = Date.now();

	let completed = 0;
	let totalTurns = 0;

	for (let i = 0; i < NUM_THREADS; i++) {
		const worker = new Worker(__filename, { workerData: { battles: battlesPerThread } });

		worker.on('message', msg => {
			if (msg.error) {
				console.error(`Thread ${msg.id} error: ${msg.error}`);
				completed++;
				if (completed === NUM_THREADS) finish();
				return;
			}
			const { id, turns } = msg;
			totalTurns += turns;
			completed++;
			console.log(`Thread ${id} completed: ${turns} turns`);

			if (completed === NUM_THREADS) finish();
		});

		worker.on('error', err => {
			console.error(`Worker error: ${err}`);
			completed++;
			if (completed === NUM_THREADS) finish();
		});
	}

	function finish() {
		const elapsed = Date.now() - startTime;
		console.log(`\nAll threads finished.`);
		console.log(`Total battles: ${NUM_THREADS * battlesPerThread}`);
		console.log(`Total turns simulated: ${totalTurns}`);
		console.log(`Wall-clock time: ${elapsed} ms`);
		console.log(`Avg turns per ms: ${totalTurns / elapsed}`);
		console.log(`Avg turns per ms per thread: ${totalTurns / elapsed / NUM_THREADS}`);
	}
} else {
	// Worker thread: run battles
	const { Dex, BattleStreams, RandomPlayerAI, Teams } = require('@pkmn/sim');
	const { TeamGenerators } = require('@pkmn/randoms');

	// One-time setup per worker
	Teams.setGeneratorFactory(TeamGenerators);
	Dex.formats.get('gen9randombattle');
	const spec = { formatid: 'gen9randombattle' };

	// Simulate one battle, return turns
	async function runSingleBattle() {
		const battleStream = new BattleStreams.BattleStream();
		const { p1, p2, omniscient: omni } = BattleStreams.getPlayerStreams(battleStream);

		// Hook up AIs
		const ai1 = new RandomPlayerAI(p1);
		const ai2 = new RandomPlayerAI(p2);
		void ai1.start();
		void ai2.start();

		// Send start and player/team commands on omniscient stream
		omni.write(`>start ${JSON.stringify(spec)}\n`);
		omni.write(`>player p1 ${JSON.stringify({ name: 'Bot 1', team: Teams.pack(Teams.generate(spec.formatid)) })}\n`);
		omni.write(`>player p2 ${JSON.stringify({ name: 'Bot 2', team: Teams.pack(Teams.generate(spec.formatid)) })}\n`);

		// Count turns until battle ends
		let turns = 0;
		for await (const chunk of omni) {
			const line = chunk.toString();
			if (line.includes('|turn|')) turns++;
			if (line.includes('|win|') || line.includes('|tie|')) break;

			const v = battleStream.battle.toJSON();
			// battleStream.battle = Battle.fromJSON(v);

			if (turns === 10) {
				// console.log(battleStream.battle.toJSON());
				// battleStream.battle.toJSON();
			}
		}
		return turns;
	}

	// Worker main
	(async () => {
		try {
			const battles = workerData.battles;
			let workerTurns = 0;
			for (let i = 0; i < battles; i++) {
				workerTurns += await runSingleBattle();
			}
			parentPort.postMessage({ id: threadId, turns: workerTurns });
		} catch (err) {
			parentPort.postMessage({ id: threadId, error: err.message });
		}
	})();
}
