"""USC candidate materialization and isolated, observable-aligned worker.

No imports of the old methane/ethylene evaluators. Physics settings for macro
queries are delegated to the benchmark-owned observable adapters unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import fcntl

from .usc_protocol import atomic_json, digest, file_hash


def plain(value):
    """Cantera AnyMap cannot be deep-copied; detach nested mappings explicitly."""
    if hasattr(value, 'items'):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def residual(prediction, observation):
    value, uncertainty = observation['value'], observation['uncertainty']
    if not all(math.isfinite(x) and x > 0 for x in (prediction, value, uncertainty)):
        raise ValueError('positive finite observable and uncertainty required')
    kind = observation['uncertainty_kind']
    if kind == 'one_sigma_log_multiplicative':
        return math.log(prediction / value) / uncertainty
    if kind == 'one_sigma_absolute':
        return (prediction - value) / uncertainty
    raise ValueError(f'unsupported uncertainty definition: {kind}')


def reaction_id(index, reaction):
    # Parent index disambiguates duplicate equations, and the parent hash binds
    # that index. Never use a pruned candidate's numeric index for edits.
    return f'{index}:{reaction.reaction_type}:{reaction.equation}'


class CandidateFactory:
    def __init__(self, parent_path, output, protected, rate_bounds=None):
        import cantera as ct
        self.parent_path = Path(parent_path).resolve()
        self.parent_hash = file_hash(parent_path)
        self.gas = ct.Solution(str(parent_path))
        if self.gas.thermo_model != 'ideal-gas':
            raise ValueError('USC factory supports ideal-gas parents only; no silent EOS replacement')
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.protected = set(protected)
        if not self.protected <= set(self.gas.species_names):
            raise ValueError('parent lacks required boundary species')
        self.rate_bounds = rate_bounds or {}
        self.ids = [reaction_id(i, r) for i, r in enumerate(self.gas.reactions())]
        self.parent = self.materialize(self.gas.species_names, {})

    def materialize(self, keep, multipliers):
        import cantera as ct
        from .mechanism_closure import reaction_participant_names
        keep = set(keep)
        if not self.protected <= keep:
            raise ValueError(f'protected boundary species removed: {sorted(self.protected - keep)}')
        if not keep <= set(self.gas.species_names):
            raise ValueError('unknown species')
        for rid, factor in multipliers.items():
            bound = self.rate_bounds.get(rid)
            if (not bound or bound.get('parent_sha256') != self.parent_hash
                    or not bound.get('source') or not bound.get('review_reference')):
                raise ValueError('rate edit lacks source-backed, parent-bound reviewed bounds')
            if not math.isfinite(factor) or not 0 < bound['lower'] <= factor <= bound['upper']:
                raise ValueError('rate multiplier outside reviewed bounds')
        spec = {'parent_sha256': self.parent_hash, 'keep': sorted(keep), 'multipliers': multipliers}
        candidate_id = digest(spec)
        path = self.output / (candidate_id + '.yaml')
        species = [s for s in self.gas.species() if s.name in keep]
        shell = ct.Solution(thermo='ideal-gas', kinetics='gas', species=species)
        reactions, retained = [], []
        for rid, r in zip(self.ids, self.gas.reactions()):
            if not (reaction_participant_names(r) | set(r.orders)) <= keep:
                continue
            data = plain(r.input_data)
            if 'efficiencies' in data:
                data['efficiencies'] = {k: v for k, v in data['efficiencies'].items() if k in keep}
            if rid in multipliers:
                factor = multipliers[rid]
                changed = False
                for key in ('rate-constant', 'high-P-rate-constant', 'low-P-rate-constant'):
                    if key in data and 'A' in data[key]:
                        data[key]['A'] *= factor
                        changed = True
                if 'rate-constants' in data:
                    for rate in data['rate-constants']:
                        rate['A'] *= factor
                    changed = True
                if not changed:
                    raise ValueError('unsupported rate type; no silent approximation')
            reactions.append(ct.Reaction.from_dict(data, shell))
            retained.append(rid)
        if set(multipliers) - set(retained):
            raise ValueError('rate edit targets deleted or unknown reaction')
        if not reactions:
            raise ValueError('empty reaction mechanism')
        gas = ct.Solution(thermo='ideal-gas', kinetics='gas', species=species,
                          reactions=reactions, transport_model='mixture-averaged')
        # Reaction construction checks elemental conservation. Additional
        # nonphysical runtime outcomes are reported separately by the worker.
        with path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not path.exists():
                temporary = path.with_suffix('.pending.yaml')
                gas.write_yaml(str(temporary))
                temporary.replace(path)
        loaded = ct.Solution(str(path))
        if loaded.species_names != gas.species_names or loaded.reaction_equations() != gas.reaction_equations():
            raise ValueError('candidate roundtrip identity mismatch')
        import numpy as np
        for temperature in (500., 1000., 1800.):
            for pressure in (101325., 1013250.):
                for item in (gas, loaded):
                    item.TPX = temperature, pressure, {s: 1 for s in keep}
                for attribute in ('forward_rate_constants', 'reverse_rate_constants',
                                  'equilibrium_constants', 'standard_cp_R', 'standard_enthalpies_RT',
                                  'standard_entropies_R', 'mix_diff_coeffs', 'viscosity', 'thermal_conductivity'):
                    expected, actual = np.asarray(getattr(gas, attribute)), np.asarray(getattr(loaded, attribute))
                    if (not np.all(np.isfinite(expected)) or not np.all(np.isfinite(actual))
                            or not np.allclose(expected, actual, rtol=1e-10, atol=0)):
                        raise ValueError('candidate roundtrip mismatch: ' + attribute)
        return {'id': candidate_id, **spec, 'path': str(path.resolve()), 'sha256': file_hash(path),
                'species_count': loaded.n_species, 'reaction_count': loaded.n_reactions,
                'reaction_ids': retained, 'roundtrip_passed': True}


def _importance(gas, states, targets, *, solver_mass_atol=0., diagnostics=None):
    """Qualified upstream DRGEP, with explicit TPX-to-TPY conversion."""
    from .physical_tools import drgep_rank
    return drgep_rank(gas, states, targets, solver_mass_atol=solver_mass_atol, diagnostics=diagnostics)


def worker(job):
    import cantera as ct
    from .physical_tools import RankSampleError
    import numpy as np
    import resource
    from .observable_adapters import constant_volume_idt_max_dpdt, premixed_laminar_flame_speed_composition
    from .usc_idt_window import resolve_idt, validate_policy
    policy = validate_policy(job.get('numerical_policy'))
    started, cpu = time.monotonic(), time.process_time()
    seconds = float(job['max_cpu_seconds'])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('positive explicit job CPU budget required')
    ceiling = math.ceil(time.process_time() + seconds)
    resource.setrlimit(resource.RLIMIT_CPU, (ceiling, ceiling + 1))
    case, candidate = job['case'], job['candidate']
    if file_hash(candidate['path']) != candidate['sha256']:
        raise ValueError('candidate hash mismatch')
    state = case['initial_state']
    family = case['operator']['family']
    expected = {'ignition_delay': ('adiabatic_constant_volume_homogeneous_reactor_0d', 's'),
                'laminar_flame_speed': ('freely_propagating_adiabatic_premixed_flame_1d', 'm/s')}
    if family not in expected or (case['operator']['model'], case['observation']['unit']) != expected[family]:
        raise ValueError('unsupported observable model or unit')
    if family == 'ignition_delay' and case['operator']['observable_definition'] != 'max grad pres':
        raise ValueError('unsupported ignition marker')
    # Flame labels identify source measurement procedures; the declared model
    # is the benchmark's common unstretched FreeFlame adapter, not a claim to
    # replay heat-flux/counterflow apparatus diagnostics.
    row = {'case_id': case['case_id'], 'candidate_id': candidate['id'],
           'operation': job['operation'], 'solver_version': ct.__version__, 'solver_invocations': 1}
    try:
        if job['operation'] == 'query':
            if family == 'ignition_delay':
                row['idt_attempts'] = []
                result = resolve_idt(candidate['path'], case, policy, row['idt_attempts'])
            else:
                result = premixed_laminar_flame_speed_composition(candidate['path'],
                    mole_fractions=state['mole_fractions'], unburned_temperature_k=state['temperature_k'],
                    pressure_pa=state['pressure_pa'])
            row.update(status='success', prediction=result.value,
                       signed_sigma=residual(result.value, case['observation']), diagnostics=result.as_dict())
        elif job['operation'] == 'rank':
            from .physical_tools import validate_targets, ranking_implementation, ranking_diagnostics
            gas = ct.Solution(candidate['path'])
            validate_targets(job['targets'], gas.species_names)
            provenance = ranking_implementation()
            gas.TPX = state['temperature_k'], state['pressure_pa'], state['mole_fractions']
            samples = []
            if family == 'ignition_delay':
                window = max(8 * case['observation']['value'], 2e-4)
                if policy:
                    row['idt_attempts'] = []
                    resolved = resolve_idt(candidate['path'], case, policy, row['idt_attempts'])
                    window = resolved.diagnostics['t_end_s']
                row['rank_window_s'] = window
                reactor = ct.IdealGasReactor(gas)
                net = ct.ReactorNet([reactor])
                net.atol = policy.get('idt_rank_integrator_atol',1e-12)
                net.rtol = policy.get('idt_rank_integrator_rtol',1e-8)
                sample_mass_atol = policy.get('idt_rank_sample_mass_bound',net.atol)
                row['rank_integrator'] = dict(atol=net.atol,rtol=net.rtol,
                    sample_mass_bound=sample_mass_atol,
                    review=policy.get('rank_precision_review','legacy_atol_coupled'))
                for t in np.linspace(0, window, 32)[1:]:
                    net.advance(float(t))
                    samples.append((reactor.T, reactor.thermo.P, reactor.thermo.X.copy()))
            else:
                flame = ct.FreeFlame(gas, width=.03)
                flame.transport_model = 'mixture-averaged'
                flame.set_refine_criteria(ratio=4., slope=.1, curve=.1, prune=.02)
                flame.solve(loglevel=0, auto=True)
                sample_mass_atol = min(flame.flame.steady_abstol(s) for s in gas.species_names)
                for i in np.unique(np.linspace(0, len(flame.grid)-1, 32).astype(int)):
                    samples.append((flame.T[i], flame.P, flame.X[:, i].copy()))
            normalization = {}
            importance = _importance(gas, samples, job['targets'], solver_mass_atol=sample_mass_atol,
                                     diagnostics=normalization)
            row.update(status='success', importance=importance, sample_count=len(samples),
                       ranking_implementation=provenance, ranking_targets=sorted(job['targets']),
                       ranking_diagnostics=ranking_diagnostics(importance),
                       sample_normalization=normalization,
                       sampling_basis='TPX converted to TPY by Cantera; each state searched independently')
        else:
            raise ValueError('unknown worker operation')
    except RankSampleError as exc:
        # Preserve the prior failure category, but do not discard which solver
        # sample was rejected when the process envelope serializes the result.
        row.update(status='worker_error', error=str(exc),
                   failure_kind='rank_sample_outside_declared_tolerance',
                   sample_normalization=exc.diagnostics)
    except ct.CanteraError as exc:
        row.update(status='numerical_failure', error=str(exc)[:2000])
    except RuntimeError as exc:
        row.update(status='unresolved_observable', error=str(exc)[:2000])
    row.update(cpu_seconds=time.process_time()-cpu, wall_seconds=time.monotonic()-started)
    return row


class LocalBackend:
    """Capability-scoped query access; cache lookup only follows a legal request.

    A controller never receives a filesystem/cache handle. Physical deduplication
    does not grant it the other trajectory's evidence. This is an application
    boundary, not an OS sandbox for arbitrary third-party Python controllers.
    """
    def __init__(self, cases, cache, python=sys.executable, workers=1, numerical_policy=None):
        from .usc_idt_window import validate_policy
        self.numerical_policy = validate_policy(numerical_policy)
        self._cases = {c['case_id']: deepcopy(c) for c in cases}
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.python = str(python)
        if not 1 <= workers <= 8:
            raise ValueError('solver workers per trajectory must be 1..8')
        self.workers = workers

    def batch_grants(self, ids, budget):
        from .budget_recovery import weighted_grants
        return weighted_grants(self._cases,ids,budget)

    def execute_batch(self, candidate, ids, operation, targets, max_cpu_seconds):
        """Independent jobs with disjoint CPU grants; completed rows are yielded.

        No oversubscription inside solvers. With 9 trajectories x 8 workers the
        explicit scheduler limit is 72 solver processes, below the 96-core cap.
        Parallel results are released in input order for deterministic policy
        observations, not in timing-dependent completion order.
        """
        if set(ids) - self._cases.keys():
            raise PermissionError('batch contains a case outside capability')
        if not ids:
            return
        from concurrent.futures import ProcessPoolExecutor
        grants = self.batch_grants(ids,max_cpu_seconds)
        jobs = [(self._cases[cid], str(self.cache), self.python, candidate, operation, targets,
                 grant, self.numerical_policy) for cid, grant in zip(ids, grants)]
        with ProcessPoolExecutor(max_workers=min(self.workers, len(ids))) as pool:
            for row in pool.map(_isolated_batch_query, jobs):
                yield row

    def execute(self, candidate, case_id, operation, targets, max_cpu_seconds):
        if case_id not in self._cases:
            raise PermissionError('case is outside this optimization capability')
        if not math.isfinite(max_cpu_seconds) or max_cpu_seconds <= 0:
            raise ValueError('explicit positive CPU grant required')
        lock_id = digest([candidate['sha256'], self._cases[case_id], operation, targets])
        with (self.cache/(lock_id+'.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._execute_locked(candidate, case_id, operation, targets, max_cpu_seconds)

    def _execute_locked(self, candidate, case_id, operation, targets, max_cpu_seconds):
        if case_id not in self._cases:
            raise PermissionError('case is outside this optimization capability')
        if file_hash(candidate['path']) != candidate['sha256']:
            raise ValueError('candidate artifact changed')
        case = self._cases[case_id]
        import cantera as ct
        rank_source = None
        if operation == 'rank':
            from .physical_tools import ranking_implementation, validate_targets
            validate_targets(targets, candidate['keep'])
            rank_source = ranking_implementation()
        key = digest({'candidate': candidate['sha256'], 'case': case, 'operation': operation,
                      'targets': targets, 'version': ct.__version__,
                      'ranking_implementation': rank_source,
                      'numerical_policy': self.numerical_policy,
                      'window_source': file_hash(Path(__file__).with_name('usc_idt_window.py')),
                      'backend_source': file_hash(__file__),
                      'adapter_source': file_hash(Path(__file__).with_name('observable_adapters.py'))})
        path = self.cache / (key + '.json')
        if path.exists():
            row = json.loads(path.read_text())
            if row.get('source_id') != key or row.get('case_id') != case_id or row.get('candidate_id') != candidate['id']:
                raise ValueError('cache identity mismatch')
            if row['logical_cpu_seconds'] > max_cpu_seconds:
                return {'status': 'budget_unavailable', 'case_id': case_id, 'candidate_id': candidate['id'],
                        'logical_cpu_seconds': 0., 'actual_cpu_seconds': 0., 'solver_invocations': 0}
            row.update(cache_hit=True, actual_cpu_seconds=0., solver_invocations=0)
            return row
        payload = {'candidate': candidate, 'case': case, 'operation': operation, 'targets': targets,
                   'max_cpu_seconds': max_cpu_seconds, 'numerical_policy': self.numerical_policy}
        import resource
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        env = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
        result = subprocess.run([self.python, '-m', 'mechrl.usc_backend'], input=json.dumps(payload),
                                text=True, capture_output=True, env=env,
                                cwd=Path(__file__).resolve().parents[1])
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        actual = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
        if result.returncode:
            cpu_limit_hit = result.returncode == -24 or (result.returncode == -9 and actual >= max_cpu_seconds)
            status = 'budget_censored' if cpu_limit_hit else 'worker_error'
            row = {'status': status, 'case_id': case_id, 'candidate_id': candidate['id'],
                   'operation': operation, 'solver_invocations': 1, 'error': result.stderr[-2000:]}
        else:
            try:
                row = json.loads(result.stdout)
                if row['case_id'] != case_id or row['candidate_id'] != candidate['id'] or row['operation'] != operation:
                    raise ValueError('worker response identity mismatch')
            except (ValueError, KeyError):
                row = {'status': 'worker_error', 'case_id': case_id, 'candidate_id': candidate['id'],
                       'operation': operation, 'solver_invocations': 1, 'error': 'invalid worker response'}
        row.update(logical_cpu_seconds=actual, actual_cpu_seconds=actual, cache_hit=False,
                   elapsed_wall_seconds=time.monotonic()-started, source_id=key)
        if row['status'] == 'success':
            atomic_json(path, row)
        return row


def _isolated_batch_query(job):
    case, cache, python, candidate, operation, targets, grant, policy = job
    return LocalBackend([case], cache, python, numerical_policy=policy).execute(
        candidate, case['case_id'], operation, targets, grant)


if __name__ == '__main__':
    print(json.dumps(worker(json.load(sys.stdin)), allow_nan=False))
