import sys, os
if getattr(sys, '_is_gil_enabled', lambda: False)():
    os.environ['PYTHON_GIL'] = '0'
    os.execv(sys.executable, [sys.executable] + sys.argv)

"""run_analysis.py — Full analysis pipeline.

For each level multiplier (0.6, 0.7, 0.8, 0.9, 1.0):
  1. Generate (or load cached) MatchupInfo with parallel processing.
  2. Select best, median, and worst teams.
  3. Run one recorded battle for each (team × iterations × depth) combo.
  4. Write a human-readable analysis report for each battle.

Output directories:
  matchup/    — pickled MatchupInfo objects
  raw_data/   — JSON battle records from play_game(record_path=...)
  analysis/   — txt analysis reports from battle_analysis

File naming:
  multiplier 0.6 (60% of natural levels) → lv36 prefix
  multiplier 0.7 → lv42, 0.8 → lv48, 0.9 → lv54, 1.0 → lv60
"""

import itertools
import json
import os
import pickle

from MatchupInfo import MatchupInfo, get_teams_at_level
from NodeIPC import NodeIPC
from battle_analysis import format_battle_report
from battle_sim import (
    PokemonMCTS, play_game, assemble_team_string,
    assemble_opponent_string, reorder_team,
)
from test2 import build_scores

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NODE_SCRIPT = "/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js"

LEVEL_MULTIPLIERS = [0.6, 0.7, 0.8, 0.9, 1.0]
ITERATIONS = [100, 1000, 10000]
DEPTHS = [0, 10, 20, 50, 100, 1000]
NUM_WORKERS = 30


def _lv_str(multiplier: float) -> str:
    """Convert a multiplier to a level label: 0.6 → 'lv36', 1.0 → 'lv60'."""
    return f"lv{int(100 * multiplier)}"


# ---------------------------------------------------------------------------
# Team selection
# ---------------------------------------------------------------------------

def select_teams(m) -> tuple:
    """Return (best, median, worst) team result tuples from prunedMatchupInfo.

    Each result is a (score, team_tuple, assignment_dict) as returned by
    the assignment solver. Uses all C(N, 6) team combinations.
    """
    raw_scores, normalized, my_pokemon, opponents = build_scores(m.prunedMatchupInfo)
    n_opp = len(opponents)
    opp_indices = list(range(n_opp))

    results = []
    for team in itertools.combinations(my_pokemon, 6):
        matrix = [[normalized[p][opp] for opp in opponents] for p in team]
        best_score = -1.0
        best_perm = None
        for perm in itertools.permutations(opp_indices):
            score = sum(matrix[i][perm[i]] for i in range(n_opp))
            if score > best_score:
                best_score = score
                best_perm = perm
        assignment = {team[i]: opponents[best_perm[i]] for i in range(n_opp)}
        results.append((best_score, team, assignment))

    results.sort(key=lambda x: x[0], reverse=True)
    n = len(results)
    return results[0], results[n // 2], results[-1]  # best, median, worst


# ---------------------------------------------------------------------------
# Single simulation job
# ---------------------------------------------------------------------------

def _run_sim_job(job: dict) -> None:
    """Run one recorded battle and write the analysis report."""
    json_path = job["json_path"]
    analysis_path = job["analysis_path"]
    node_script = job["node_script"]
    player_str = job["player_str"]
    opp_str = job["opp_str"]
    iters = job["iters"]
    depth = job["depth"]
    num_workers = job["num_workers"]

    if os.path.exists(json_path):
        # Load existing JSON for analysis only
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        # Run the battle
        ipc = NodeIPC(node_script)
        try:
            mcts = PokemonMCTS(ipc, simulation_depth_limit=depth,
                               node_script_path=node_script, num_workers=num_workers)
            # record_path without .json extension — play_game appends it
            record_path = json_path[:-5] if json_path.endswith(".json") else json_path
            play_game(ipc, player_str, opp_str, mcts, iters, record_path=record_path)
        finally:
            ipc.close()

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

    # Write analysis report
    if not os.path.exists(analysis_path):
        data["matchup_scores"] = job.get("matchup_scores")
        report = format_battle_report(data)
        with open(analysis_path, "w", encoding="utf-8") as f:
            f.write(report)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    os.makedirs("matchup", exist_ok=True)
    os.makedirs("raw_data", exist_ok=True)
    os.makedirs("analysis", exist_ok=True)

    for multiplier in LEVEL_MULTIPLIERS:
        lv = _lv_str(multiplier)
        print(f"\n{'=' * 60}")
        print(f"Level multiplier: {multiplier}  ({lv})")
        print(f"{'=' * 60}")

        # ----------------------------------------------------------------
        # Step 1: Generate or load matchup info
        # ----------------------------------------------------------------
        pkl_path = f"matchup/matchup_{lv}.pkl"
        if os.path.exists(pkl_path):
            print(f"Loading cached matchup from {pkl_path}...")
            with open(pkl_path, "rb") as f:
                m = pickle.load(f)
        else:
            print(f"Generating matchup (this may take a while)...")
            m = MatchupInfo(NODE_SCRIPT, level_multiplier=multiplier,
                            num_workers=NUM_WORKERS)
            with open(pkl_path, "wb") as f:
                pickle.dump(m, f)
            print(f"Saved matchup to {pkl_path}")

        # ----------------------------------------------------------------
        # Step 2: Select teams + compute matchup scores
        # ----------------------------------------------------------------
        print("Selecting best, median, and worst teams...")
        raw_scores, normalized, _, opponents = build_scores(m.prunedMatchupInfo)
        best, median, worst = select_teams(m)
        box, opp = get_teams_at_level(multiplier)
        opp_first = next(iter(opp))

        teams = [("best", best), ("median", median), ("worst", worst)]
        for team_type, (score, team, _) in teams:
            print(f"  {team_type}: score={score:.4f}  members={list(team)}")

        # ----------------------------------------------------------------
        # Step 3: Build simulation jobs
        # ----------------------------------------------------------------
        jobs = []
        for team_type, team_result in teams:
            score, team, assignment = team_result
            ordered = reorder_team(list(team), assignment, opp_first)
            player_str = assemble_team_string(ordered, box)
            opp_str = assemble_opponent_string(opp_first, opp)

            team_matchup = {
                p: [
                    {
                        "opponent": opp_name,
                        "raw": raw_scores[p][opp_name],
                        "normalized": normalized[p][opp_name],
                        "assigned": assignment.get(p) == opp_name,
                    }
                    for opp_name in opponents
                ]
                for p in team
            }

            for iters in ITERATIONS:
                for depth in DEPTHS:
                    base = f"battle_{lv}_{team_type}_iter{iters}_depth{depth}"
                    jobs.append({
                        "node_script": NODE_SCRIPT,
                        "player_str": player_str,
                        "opp_str": opp_str,
                        "iters": iters,
                        "depth": depth,
                        "num_workers": NUM_WORKERS,
                        "matchup_scores": team_matchup,
                        "json_path": f"raw_data/{base}.json",
                        "analysis_path": f"analysis/{base}_analysis.txt",
                    })

        # ----------------------------------------------------------------
        # Step 4: Run simulations + analysis in parallel
        # ----------------------------------------------------------------
        total = len(jobs)
        print(f"Running {total} simulations ({NUM_WORKERS} parallel MCTS workers each)...")
        completed = 0
        for job in jobs:
            _run_sim_job(job)
            completed += 1
            if completed % 8 == 0 or completed == total:
                print(f"  {completed}/{total} done")

        print(f"Level {lv} complete.")

    print("\nAll levels complete.")
    print(f"  {len(LEVEL_MULTIPLIERS) * len(ITERATIONS) * len(DEPTHS) * 3} battles recorded in raw_data/")
    print(f"  Analysis reports written to analysis/")


if __name__ == "__main__":
    main()
