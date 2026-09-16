"""Read-only replay of historical Solo graph scores; never solves or edits a run.

Run with the Solo run's own Python environment and pass its work directory.
Only the recorded importance() function is extracted; workflow top-level code
is not executed. The alternative graph representation exists only in memory.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import time
import warnings

import cantera as ct
import numpy as np
import scipy
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


def sparse_search(cost, **kwargs):
    row, col = np.where(np.isfinite(cost))
    graph = csr_matrix((cost[row, col], (row, col)), shape=cost.shape)
    return dijkstra(graph, **kwargs)


def extract_importance(source, search):
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "importance")
    namespace = {"np": np, "dijkstra": search}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["importance"]


def replay(root):
    start = time.process_time()
    source = root / "scripts/workflow.py"
    original = extract_importance(source, dijkstra)
    alternative = extract_importance(source, sparse_search)
    reference = ct.Solution(str(root / "inputs/parent.yaml"))
    species_old = np.zeros(reference.n_species)
    species_new = species_old.copy()
    reactions_old = np.zeros(reference.n_reactions)
    reactions_new = reactions_old.copy()
    paths = sorted((root / "results/parent_baseline/parent").glob("*train*.npz"))
    rows = []
    for path in paths:
        metadata = json.loads(path.with_suffix(".json").read_text())
        case = metadata["case"]
        gas = ct.Solution(str(root / "inputs/parent.yaml"))
        gas.TP = case["T"], case["P"]
        gas.set_equivalence_ratio(case["phi"], case["fuel"], case["oxidizer"])
        supported = {e for e in gas.element_names if gas.elemental_mass_fraction(e) > 0}
        feasible = np.array([set(s.composition) <= supported for s in gas.species()])
        with np.load(path) as data:
            if case["kind"] == "flame":
                indices = np.flatnonzero((data["T"] > case["T"] + 5)
                                         & (data["T"] < max(data["T"]) - 10))
                indices = indices[np.unique(np.linspace(0, len(indices) - 1,
                                             min(120, len(indices))).astype(int))]
                states = [(data["T"][i], data["P"][i], data["Y"][:, i]) for i in indices]
            else:
                indices = data["sample_indices"]
                selected = [j for j, i in enumerate(indices)
                            if .001 * metadata["tau"] <= data["t"][i] <= 1.2 * metadata["tau"]]
                states = [(data["T"][indices[j]], data["P"][indices[j]], data["Y_sample"][j])
                          for j in selected]
            clean = []
            for temperature, pressure, mass_fractions in states:
                values = np.maximum(mass_fractions, 0) * feasible
                values /= values.sum()
                clean.append((temperature, pressure, values))
        targets = list(case["fuel"]) + ["O2", "H", "OH", "HO2", "CO"]
        before = original(gas, clean, targets, clean=True)
        after = alternative(gas, clean, targets, clean=True)
        with np.load(root / "results/rescored/parent" / path.name) as saved:
            replay_error = float(np.max(np.abs(before[0] - saved["species_score"])))
        species_old = np.maximum(species_old, before[0])
        species_new = np.maximum(species_new, after[0])
        reactions_old = np.maximum(reactions_old, before[1])
        reactions_new = np.maximum(reactions_new, after[1])
        rows.append({"case": case["id"], "states": len(clean),
                     "original_vs_saved_max": replay_error,
                     "species_score_change_max": float(np.max(abs(before[0] - after[0])))})

    changed = [{"species": name, "original": float(a), "sparse": float(b)}
               for name, a, b in zip(reference.species_names, species_old, species_new)
               if abs(a - b) > 1e-8]
    matrix = np.array([[0, 1e-100, np.inf], [np.inf, 0, -np.log(.8)], [np.inf, np.inf, 0.]])
    return {
        "scope": "Read-only graph replay on existing cleaned training states; no simulation, new candidate, or search",
        "versions": {"cantera": ct.__version__, "scipy": scipy.__version__, "numpy": np.__version__},
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "synthetic_dense_scores": np.exp(-dijkstra(matrix, directed=True, indices=[0])).tolist(),
        "synthetic_sparse_scores": np.exp(-sparse_search(matrix, directed=True, indices=[0])).tolist(),
        "n_cases": len(rows), "n_states": sum(r["states"] for r in rows),
        "original_vs_saved_max": max(r["original_vs_saved_max"] for r in rows),
        "n_cases_with_species_change_gt_1e8": sum(r["species_score_change_max"] > 1e-8 for r in rows),
        "aggregate_species_changes_gt_1e8": changed,
        "threshold_membership_changes_before_protection": {
            str(t): [s for s, a, b in zip(reference.species_names, species_old, species_new)
                     if (a >= t) != (b >= t)] for t in [.001, .003, .01, .02, .04, .07, .1, .15, .2, .3]
        },
        "aggregate_reaction_score_change_max": float(np.max(abs(reactions_old - reactions_new))),
        "reaction_threshold_membership_change_counts": {
            str(t): int(np.sum((reactions_old >= t) != (reactions_new >= t)))
            for t in [.001, .003, .01, .02, .03, .05, .1]
        },
        "cpu_seconds": time.process_time() - start,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    warnings.filterwarnings("ignore", category=UserWarning)
    print(json.dumps(replay(args.work.resolve()), ensure_ascii=False, indent=2))
