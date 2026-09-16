"""USC-only, explicitly selected finite-window policy; shared adapter unchanged."""
from copy import deepcopy

V2_POLICY = {'idt_window_factors': [1, 10, 100], 'idt_base_samples': 4000}
RANK_PRECISION_POLICY = {**V2_POLICY,
    'idt_rank_integrator_atol': 1e-14, 'idt_rank_integrator_rtol': 1e-10,
    'idt_rank_sample_mass_bound': 1e-12,
    'rank_precision_review': 'ER-20260909-USC-RANK-PRECISION'}


def validate_policy(policy):
    if policy not in (None, {}, V2_POLICY, RANK_PRECISION_POLICY):
        raise ValueError('unsupported USC numerical policy')
    return deepcopy(policy or {})


def resolve_idt(mechanism, case, policy=None, attempts=None, adapter=None):
    """All attempts remain inside the enclosing worker's one CPU grant.

    Only known unresolved-observable errors permit extension. Numerical and
    programming errors propagate. A CPU-killed worker remains censored and may
    not have a complete attempt log; no fabricated internal solve count.
    """
    validate_policy(policy)
    if adapter is None:
        from .observable_adapters import constant_volume_idt_max_dpdt
        adapter = constant_volume_idt_max_dpdt
    attempts = attempts if attempts is not None else []
    state = case['initial_state']
    base = max(8*case['observation']['value'], 2e-4)
    factors = policy['idt_window_factors'] if policy else [1]
    for factor in factors:
        entry = {'factor': factor, 't_end_s': base*factor, 'sample_count': 4000*factor}
        attempts.append(entry)
        try:
            result = adapter(mechanism, temperature_k=state['temperature_k'],
                pressure_pa=state['pressure_pa'], mole_fractions=state['mole_fractions'],
                t_end_s=entry['t_end_s'], sample_count=entry['sample_count'])
        except RuntimeError as exc:
            entry.update(status='unresolved', error=str(exc)[:2000])
            if (not str(exc).startswith('no resolved ignition:')
                    and str(exc) != 'maximum pressure-rise rate lies at integration boundary'):
                raise
            if factor == factors[-1]:
                raise
        else:
            entry['status'] = 'success'
            return result
