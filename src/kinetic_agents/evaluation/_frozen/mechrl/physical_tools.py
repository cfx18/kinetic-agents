"""Checked upstream chemistry boundaries, not another graph implementation.

Targets, mandatory retained species, and observable error are separate concepts.
No default target set or chemical acceptance threshold is invented here.
"""
from __future__ import annotations
import hashlib
from importlib import metadata
import inspect
import math
from pathlib import Path
import numpy as np

RANKING_SCHEMA = 'pymars-drgep-statewise-v1'
PYMARS_VERSION = '1.2.0'


class RankSampleError(ValueError):
    """Rejected solver sample, retaining actionable diagnostics without repair."""
    def __init__(self, message, diagnostics):
        from copy import deepcopy
        super().__init__(message)
        self.diagnostics = deepcopy(diagnostics)


def native_chemistry_guard():
    """A version string cannot detect a locally disabled chemical invariant."""
    import cantera as ct
    base = ct.Solution('h2o2.yaml')
    bad = ct.Reaction(equation='H2 => H', rate=ct.ArrheniusRate(1., 0., 0.))
    try:
        ct.Solution(thermo='ideal-gas', kinetics='gas',
                    species=[base.species('H2'), base.species('H')], reactions=[bad])
    except ct.CanteraError as exc:
        if 'unbalanced' not in str(exc).lower():
            raise RuntimeError('native chemistry probe failed for an unrelated reason') from exc
        return {'solver_version': ct.__version__, 'unbalanced_reaction_rejected': True}
    raise RuntimeError('UNBALANCED_REACTION_ACCEPTED: solver chemistry guard is disabled; do not execute')


def validate_targets(targets, species):
    if (not isinstance(targets, (list, tuple)) or not targets
            or any(not isinstance(s, str) or not s for s in targets)
            or len(set(targets)) != len(targets) or not set(targets) <= set(species)):
        raise ValueError('rank requires explicit nonempty unique target_species present in candidate; '
                         'protected inlet species are NOT an automatic target set')
    return sorted(targets)


def pymars_api():
    try:
        version = metadata.version('nrg-pymars')
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError('install pinned nrg-pymars==1.2.0; no hand-written ranking fallback') from exc
    if version != PYMARS_VERSION:
        raise RuntimeError(f'unqualified nrg-pymars version: {version}')
    from pymars import drgep
    return drgep


def ranking_implementation():
    api = pymars_api()
    source = Path(inspect.getfile(api))
    return {'schema': RANKING_SCHEMA, 'distribution': 'nrg-pymars', 'version': PYMARS_VERSION,
            'dependencies': {name: metadata.version(name) for name in ('cantera','numpy','networkx')},
            'upstream_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'adapter_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def drgep_rank(gas, states, targets, *, solver_mass_atol=0., diagnostics=None):
    """TPX samples -> upstream TPY matrices -> statewise upstream graph search.

pyMARS takes MASS fractions; archived samples contain MOLE fractions.
    Cantera converts them. Initial/user mixtures remain strictly nonnegative.
    Trusted solver samples may contain negative traces within the declared
    mass-fraction bound (legacy: integrator atol; reviewed precision policy:
    fixed original bound with tighter integration). The solver_mass_atol name
    is retained for API compatibility. Native Cantera TPX normalization is then
    used (as in the previous worker), with the raw discrepancy recorded; larger
    negatives fail. This is not a physical-validity certificate for that state.
"""
    targets = validate_targets(targets, gas.species_names)
    api = pymars_api()
    if not math.isfinite(solver_mass_atol) or solver_mass_atol < 0:
        raise ValueError('invalid declared solver absolute tolerance')
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(solver_mass_atol=solver_mass_atol, raw_negative_entries=0,
                       minimum_raw_mass_fraction=0., maximum_native_mass_fraction_change=0.,
                       normalization='native Cantera TPX/TPY; not an adapter clipping or macro-error repair')
    matrices = []
    old_state = gas.state.copy()
    try:
        for sample_index, (temperature, pressure, fractions) in enumerate(states):
            fractions = np.asarray(fractions, dtype=float)
            if (not math.isfinite(temperature) or temperature <= 0
                    or not math.isfinite(pressure) or pressure <= 0
                    or fractions.shape != (gas.n_species,)
                    or not np.all(np.isfinite(fractions))
                    or fractions.sum() <= 0):
                raise ValueError('invalid TPX rank sample; no silent clipping/repair')
            gas.set_unnormalized_mole_fractions(fractions)
            raw_y = gas.Y.copy()
            diagnostics['raw_negative_entries'] += int(np.sum(raw_y < 0))
            diagnostics['minimum_raw_mass_fraction'] = min(diagnostics['minimum_raw_mass_fraction'], float(raw_y.min()))
            if np.any(raw_y < -solver_mass_atol):
                rejected = np.flatnonzero(raw_y < -solver_mass_atol)
                diagnostics['rejected_sample'] = {
                    'sample_index': sample_index, 'temperature_k': float(temperature),
                    'pressure_pa': float(pressure), 'rejected_species_count': len(rejected),
                    'entries': [{'species': gas.species_names[i], 'raw_mass_fraction': float(raw_y[i])}
                                for i in rejected[:20]],
                    'entries_truncated': len(rejected) > 20,
                    'boundary': 'First rejected sample only; no clipping, retry, or case exclusion.'}
                raise RankSampleError('negative sampled mass fraction exceeds declared solver absolute tolerance', diagnostics)
            gas.TPX = temperature, pressure, fractions
            diagnostics['maximum_native_mass_fraction_change'] = max(
                diagnostics['maximum_native_mass_fraction_change'], float(np.max(np.abs(gas.Y-raw_y))))
            matrix = api.create_drgep_matrix((temperature, pressure, gas.Y.copy()), gas)
            if (matrix.shape != (gas.n_species, gas.n_species)
                    or not np.all(np.isfinite(matrix)) or np.any(matrix < 0)
                    or np.any(matrix > 1 + 1e-12)):
                raise ValueError('upstream DRGEP returned invalid coefficients')
            matrices.append(matrix)
        if not matrices:
            raise ValueError('rank requires at least one sampled state')
        # Never replace the separate matrices with their elementwise maximum.
        result = api.get_importance_coeffs(gas.species_names, targets, matrices)
        if set(result) != set(gas.species_names) or any(
                not math.isfinite(v) or not 0 <= v <= 1 + 1e-12 for v in result.values()):
            raise ValueError('upstream DRGEP returned invalid species importance')
        return {s: float(result[s]) for s in gas.species_names}
    finally:
        gas.state = old_state


def ranking_diagnostics(importance, protected=()):
    values = np.array([v for s, v in importance.items() if s not in set(protected)], dtype=float)
    if not len(values) or not np.all(np.isfinite(values)) or np.any(values < 0) or np.any(values > 1+1e-12):
        return {'status': 'UNUSABLE', 'reason': 'empty or invalid deletable-species scores',
                'removability_certified': False}
    return {'status': 'SATURATED' if np.all(values >= 1-1e-6) else 'PROPOSAL_ONLY',
            'min': float(values.min()), 'max': float(values.max()),
            'near_one_count': int(np.sum(values >= 1-1e-6)), 'count': len(values),
            'removability_certified': False,
            'meaning': 'larger means stronger target coupling; ordinal rank is NOT a deletion threshold'}


def threshold_deletions(importance, keep, protected, threshold):
    if (isinstance(threshold, bool) or not isinstance(threshold, (float, int))
            or not math.isfinite(threshold) or not 0 < threshold < 1):
        raise ValueError('DRGEP reduction requires explicit threshold strictly between 0 and 1')
    if not set(keep) <= set(importance):
        raise ValueError('incomplete rank cannot drive threshold reduction')
    if any(not math.isfinite(importance[s]) or not 0 <= importance[s] <= 1+1e-12 for s in keep):
        raise ValueError('invalid rank cannot drive threshold reduction')
    removed = sorted(s for s in set(keep)-set(protected) if importance[s] < threshold)
    if any(importance[s] >= 1-1e-6 for s in removed):
        raise ValueError('near-unit scores require independent evidence, not a near-one threshold')
    return removed


def check_rank_based_deletion(payload, importance, evidence):
    """A near-unit score cannot, by itself, justify an ordinal deletion.

Independent measured evidence may motivate a separate hypothesis; this is a
provenance gate, NOT a chemical law or a certificate that the hypothesis works.
"""
    near_one = [s for s in payload.get('species', []) if importance.get(s, 0) >= 1-1e-6]
    if not near_one:
        return
    basis = payload.get('reduction_basis', {})
    own = {r['evidence_id']: r for r in evidence if 'evidence_id' in r}
    ids = basis.get('evidence_ids', []) if isinstance(basis, dict) else []
    if (not isinstance(basis, dict) or basis.get('kind') != 'independent_hypothesis'
            or not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
            or any(i not in own or own[i].get('operation') != 'query'
                   or own[i].get('status') != 'success' for i in ids)):
        raise ValueError('near-unit DRGEP scores do not justify deleting '+str(near_one)+
                         '; cite own measured query evidence in reduction_basis '
                         '{kind: independent_hypothesis, evidence_ids: [...]} or change the proposal')


def validate_evidence_numbers(row):
    for key in ('logical_cpu_seconds', 'actual_cpu_seconds'):
        if key in row and (not math.isfinite(row[key]) or row[key] < 0):
            raise ValueError('invalid scientific cost: '+key)
    if row.get('status') == 'success' and row.get('operation') == 'query':
        if not math.isfinite(row['signed_sigma']):
            raise ValueError('successful query requires finite observable error')
        if 'prediction' in row and (not math.isfinite(row['prediction']) or row['prediction'] <= 0):
            raise ValueError('successful IDT/LFS query requires positive finite prediction')
