"""Read-only benchmark consumption and draft USC-II experiment contracts.

This module never freezes a split or admits experimental rows.  Source-connected
components are conservative: references attached to a page need not identify the
individual plotted point.  The resulting draft therefore requires expert review.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def stratum(case):
    return case['fuel_label'] + ':' + case['operator']['family']


def compatible_cases(registry, species_names):
    # These aliases are case-only transformations of the original identifiers,
    # NOT formula matching (which would collapse distinct isomers).
    aliases = {name.upper(): name for name in species_names}
    if len(aliases) != len(species_names):
        raise ValueError('ambiguous case-only species alias')
    accepted, excluded = [], []
    for original in registry['cases']:
        if original['reference_mechanism_id'] != 'ffcm2_model':
            continue
        case = deepcopy(original)
        state = case['initial_state']
        if state['composition_mode'] != 'explicit_reported_mole_fractions':
            raise ValueError('explicit experimental composition required')
        missing = sorted(set(state['mole_fractions']) - aliases.keys())
        if missing:
            excluded.append({'case_id': case['case_id'], 'missing_species': missing})
            continue
        state['mole_fractions'] = {aliases[k]: v for k, v in state['mole_fractions'].items()}
        case['fuel_label'] = case['case_id'].split('_')[1]
        case['source_reference_mechanism_id'] = case.pop('reference_mechanism_id')
        case['reference_mechanism_id'] = 'usc_ii_original'
        case['compatibility'] = 'input_species_only_not_physical_admission'
        accepted.append(case)
    return sorted(accepted, key=lambda c: c['case_id']), excluded


def source_components(cases, bibliography):
    ids = {c['case_id'] for c in cases}
    parent = {i: i for i in ids}
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    citations = defaultdict(set)
    for ref in bibliography['bibliography']:
        key = re.sub(r'[^a-z0-9]', '', ref['citation'].lower())
        citations[key].update(set(ref['case_ids']) & ids)
    referenced = set()
    for members in citations.values():
        ordered = sorted(members)
        referenced.update(ordered)
        for other in ordered[1:]:
            parent[root(other)] = root(ordered[0])
    if referenced != ids:
        raise ValueError(f'missing bibliography for {sorted(ids - referenced)}')
    groups = defaultdict(list)
    for i in sorted(ids):
        groups[root(i)].append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def draft_split(cases, components, seed=20260906, fraction=0.2):
    by_id = {c['case_id']: c for c in cases}
    remaining = Counter(stratum(c) for c in cases)
    target = round(len(cases) * fraction)
    order = list(components)
    random.Random(seed).shuffle(order)
    holdout = set()
    # Greedy closest-to-target, but never remove the last optimization case in
    # any observed fuel/operator stratum. No response values enter selection.
    for group in order:
        counts = Counter(stratum(by_id[i]) for i in group)
        if any(remaining[k] <= n for k, n in counts.items()):
            continue
        if abs(len(holdout) + len(group) - target) < abs(len(holdout) - target):
            holdout.update(group)
            remaining.subtract(counts)
    if not holdout:
        raise ValueError('source-connected groups prevent nonempty split')
    return {'optimization_ids': sorted(set(by_id) - holdout),
            'development_recheck_ids': sorted(holdout), 'seed': seed,
            'status': 'DRAFT_REQUIRES_REVIEW',
            'not_pristine_final_test': True,
            'source_components': components}


def build_protocol(registry_path, bibliography_path, parent_path):
    import cantera as ct
    gas = ct.Solution(str(parent_path))
    if (gas.n_species, gas.n_reactions) != (111, 784):
        raise ValueError('expected original USC-II 111 species / 784 reactions')
    cases, excluded = compatible_cases(json.loads(Path(registry_path).read_text()), gas.species_names)
    if len(cases) != 610:
        raise ValueError(f'approved compatibility surface changed: {len(cases)} != 610')
    split = draft_split(cases, source_components(cases, json.loads(Path(bibliography_path).read_text())))
    payload = {'schema': 'usc-three-mode-v1', 'status': 'DRAFT_REQUIRES_REVIEW',
               'registry_sha256': file_hash(registry_path), 'bibliography_sha256': file_hash(bibliography_path),
               'parent_sha256': file_hash(parent_path), 'solver_version': ct.__version__,
               'cases': cases, 'excluded': excluded, 'split': split,
               'modes': ['workflow', 'static', 'evolved'], 'seeds': [17, 117, 217], 'stages': 3,
               'budget_cpu_hours': {'setup_reference': 300, 'common_initialization': 200,
                                    'search_total': 1200, 'recheck': 200, 'reserve': 100},
               'stage_search_cpu_seconds': 1200 * 3600 / 27,
               'protected_species': sorted({s for c in cases for s in c['initial_state']['mole_fractions']}),
               'rate_bounds': {}, 'rate_status': 'LOCKED_UNTIL_SOURCE_BACKED_BOUNDS_REVIEW',
               'objective': 'worst_fuel_operator_q90_sigma_vs_species_vs_reactions',
               'initial_memory': [{'id': 'M0-cross-case', 'condition': 'all',
                   'strategy': 'Use across-case macro evidence; verify 1D during search; never treat unqueried cases as passing.'}],
               'claim_boundary': 'development infrastructure; neither benchmark freeze nor RSI evidence'}
    payload['protocol_sha256'] = digest(payload)
    return payload


def validate_protocol(protocol):
    body = {k: v for k, v in protocol.items() if k != 'protocol_sha256'}
    if digest(body) != protocol.get('protocol_sha256'):
        raise ValueError('protocol hash mismatch')
    ids = {c['case_id'] for c in protocol['cases']}
    a, b = map(set, (protocol['split']['optimization_ids'], protocol['split']['development_recheck_ids']))
    if a & b or a | b != ids:
        raise ValueError('split overlap or incomplete coverage')
    for group in protocol['split']['source_components']:
        if set(group) & a and set(group) & b:
            raise ValueError('source component crosses split')


def require_review(protocol, approval, operation):
    validate_protocol(protocol)
    if (approval.get('protocol_sha256') != protocol['protocol_sha256']
            or approval.get('reviewer') != 'user'
            or operation not in approval.get('approved_operations', [])
            or not approval.get('decision_reference')):
        raise PermissionError(f'explicit hash-bound user review required for {operation}')


def balanced_split_alternative(cases, components, seed=20260906, fraction=.2,
                               forced_optimization_ids=()):
    """Metadata-only review alternative; never replaces the current draft.

    Minimize total/operator fraction imbalance with whole source components,
    retaining at least one optimization case in every observed stratum.
    """
    import numpy as np
    by_id = {c['case_id']: c for c in cases}
    exposed = set(forced_optimization_ids)
    if not exposed <= by_id.keys():
        raise ValueError('unknown previously exposed case')
    locked = np.array([bool(set(g) & exposed) for g in components])
    strata = sorted({stratum(c) for c in cases})
    families = sorted({c['operator']['family'] for c in cases})
    counts = np.array([[sum(stratum(by_id[i]) == s for i in g) for s in strata] for g in components])
    family_counts = np.array([[sum(by_id[i]['operator']['family'] == f for i in g) for f in families] for g in components])
    totals, family_totals = counts.sum(axis=0), family_counts.sum(axis=0)
    sizes = counts.sum(axis=1)
    def objective(mask):
        if np.any(mask & locked):
            return float('inf')
        held = counts[mask].sum(axis=0)
        if np.any(held >= totals):
            return float('inf')
        by_family = family_counts[mask].sum(axis=0)
        return float((sizes[mask].sum()/len(cases)-fraction)**2
                     + np.square(by_family/family_totals-fraction).sum())
    rng = np.random.default_rng(seed)
    best, best_score = np.zeros(len(components), dtype=bool), float('inf')
    for _ in range(128):
        mask = np.zeros(len(components), dtype=bool)
        for _ in range(4):
            changed = False
            for index in rng.permutation(len(components)):
                proposal = mask.copy()
                proposal[index] = ~proposal[index]
                if objective(proposal) < objective(mask)-1e-15:
                    mask, changed = proposal, True
            if not changed:
                break
        score = objective(mask)
        if score < best_score:
            best, best_score = mask, score
    held = {i for selected, group in zip(best, components) if selected for i in group}
    return {'status': 'ALTERNATIVE_DRAFT_REQUIRES_REVIEW', 'seed': seed,
            'selection_inputs': 'source membership and operator/stratum metadata only; no responses',
            'forced_optimization_ids': sorted(exposed),
            'optimization_ids': sorted(set(by_id)-held), 'development_recheck_ids': sorted(held),
            'source_components': components, 'not_pristine_final_test': True,
            'objective_value': best_score}
