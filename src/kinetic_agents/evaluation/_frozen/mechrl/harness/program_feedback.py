"""Deterministic development feedback, not a mutable scientific acceptance gate.

No solver/LLM dependencies. Missing observations remain missing; adaptive panels
are never passed off as unbiased estimates of the full development population.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
from statistics import fmean, pstdev

from .storage import digest

OBJECTIVE = {
    'version': 'dev-mean-abs-sigma-v2',
    'status': 'DEVELOPMENT_OBJECTIVE_NOT_FROZEN_BENCHMARK',
    'case_loss': 'abs(existing signed_sigma against experimental observable)',
    'aggregation': 'equal-case arithmetic mean over the fixed target pool',
    'minimize': ['mean_absolute_standardized_error', 'species_count', 'reaction_count'],
    'constraints': 'unchanged physical invariants, observed definitions, isolation and budget',
    'search_permission': 'Explore accuracy/complexity tradeoffs; pointwise parent dominance is NOT required.',
    'coverage': 'Partial-panel means are descriptive only. Different panels are not directly comparable.',
    'uncertainty': 'Across-case SD is heterogeneity, NOT a confidence interval. Proxy is uncalibrated.',
}


def budget_report(checkpoint):
    limit = float(checkpoint['stage_limit'])
    used = float(checkpoint['logical_cpu_seconds']) - float(checkpoint['stage_start_cpu'])
    if not all(math.isfinite(v) for v in (limit, used)) or limit < 0 or used < -1e-7:
        raise ValueError('invalid authoritative budget ledger')
    remaining = max(0., limit - used)
    fraction=checkpoint.get('search_reserve_fraction',.3)
    if type(fraction) not in (int,float) or not math.isfinite(fraction) or not 0<=fraction<1:
        raise ValueError('invalid search reserve fraction')
    reserve = fraction * limit if checkpoint['search_phase'] else 0.
    return {'unit': 'CPU seconds', 'stage_limit': limit, 'stage_spent': used,
            'stage_remaining': remaining, 'reserved_from_search': reserve,
            'search_remaining': max(0., remaining - reserve),
            'trajectory_spent': checkpoint['logical_cpu_seconds'],
            'actual_trajectory_spent': checkpoint['actual_cpu_seconds'],
            'semantics': 'All remaining fields are NET of spent costs. Never subtract spent again.'}


def query_records(evidence):
    records = {}
    for row in evidence:
        if row.get('operation') != 'query':
            continue
        key = (row['candidate_id'], row['case_id'])
        # Match the scientific workspace: retain an earlier successful observation
        # rather than silently replacing it with a failed repeat.
        if key not in records or not valid_row(records[key]):
            records[key] = row
    return records


def valid_row(row):
    value = row.get('signed_sigma')
    return (row.get('status') == 'success' and isinstance(value, (float, int))
            and not isinstance(value, bool) and math.isfinite(value))


def statistics(values):
    if not values:
        return {'count': 0, 'mean': None, 'sd_across_cases': None, 'q90': None, 'max': None}
    ordered = sorted(values)
    position = .9 * (len(ordered) - 1)
    low = int(position)
    q90 = ordered[low] + (position-low) * (ordered[min(low+1, len(ordered)-1)]-ordered[low])
    return {'count': len(values), 'mean': fmean(values), 'sd_across_cases': pstdev(values),
            'q90': q90, 'max': max(values)}


def group_of(case):
    return case['fuel_label'] + ':' + case['operator']['family']


def evaluate(candidates, parent_id, cases, evidence, active_ids=None):
    """One objective implementation for search feedback AND frozen endpoint output."""
    cases = {c['case_id']: c for c in cases}
    if not cases:
        raise ValueError('empty target pool')
    records = query_records(evidence)
    if any(cid not in cases or mid not in candidates for mid, cid in records):
        raise PermissionError('foreign candidate/case in evaluation evidence')
    ids = list(candidates) if active_ids is None else list(active_ids)
    parent = candidates[parent_id]
    reports, success_sets = [], {}
    for mid in ids:
        candidate = candidates[mid]
        observed = {cid: records[(mid, cid)] for cid in cases if (mid, cid) in records}
        successes = {cid for cid, row in observed.items() if valid_row(row)}
        success_sets[mid] = successes
        groups = defaultdict(list)
        for cid in successes:
            groups[group_of(cases[cid])].append(abs(observed[cid]['signed_sigma']))
        values = [abs(observed[cid]['signed_sigma']) for cid in sorted(successes)]
        failures = sorted(set(observed)-successes)
        complete = len(successes) == len(cases)
        report = {'candidate_id': mid, 'species_count': candidate['species_count'],
            'reaction_count': candidate['reaction_count'],
            'species_reduction': 1-candidate['species_count']/parent['species_count'],
            'reaction_reduction': 1-candidate['reaction_count']/parent['reaction_count'],
            'observed_error': statistics(values),
            'full_pool_mean_error': fmean(values) if complete else None,
            'successful_cases': len(successes), 'target_cases': len(cases),
            'coverage': len(successes)/len(cases), 'queried_cases': len(observed),
            'missing_count': len(cases)-len(observed), 'failure_count': len(failures),
            'failed_case_ids': failures, 'complete': complete,
            'panel_id': digest(sorted(successes)),
            'group_errors': {g: statistics(groups[g]) for g in sorted({group_of(c) for c in cases.values()})},
            'claim': 'complete_development_pool' if complete else 'partial_descriptive_only'}
        paired = []
        for cid in sorted(successes):
            baseline = records.get((parent_id, cid), {})
            if valid_row(baseline):
                p, c = abs(baseline['signed_sigma']), abs(observed[cid]['signed_sigma'])
                paired.append({'case_id': cid, 'group': group_of(cases[cid]),
                    'parent_error': p, 'candidate_error': c, 'delta': c-p})
        report['parent_comparison'] = {
            'paired_count': len(paired),
            'mean_error_delta': fmean([r['delta'] for r in paired]) if paired else None,
            'worst_error_increase': max([r['delta'] for r in paired], default=None),
            'top_regressions': sorted(paired, key=lambda r: -r['delta'])[:3],
            'meaning': 'Same cases only, no pointwise-dominance acceptance rule.'}
        reports.append(report)
    # Use identical support to avoid rewarding cherry-picked easy panels. This
    # descriptive frontier is not the separate complete-population frontier.
    comparable = [mid for mid in ids if success_sets[mid]]
    common = set.intersection(*(success_sets[mid] for mid in comparable)) if comparable else set()
    common_ids = sorted(common)
    points = []
    for report in reports:
        if report['candidate_id'] in comparable and common_ids:
            point = {k: report[k] for k in ('candidate_id','species_count','reaction_count')}
            point['error'] = fmean([abs(records[(report['candidate_id'], cid)]['signed_sigma']) for cid in common_ids])
            point['failures_elsewhere'] = report['failure_count']
            points.append(point)

    def front(points):
        def vector(p): return (p['error'], p['species_count'], p['reaction_count'])
        return [a['candidate_id'] for a in points if not any(
            all(x <= y for x, y in zip(vector(b), vector(a))) and
            any(x < y for x, y in zip(vector(b), vector(a))) for b in points)]

    full = [{'candidate_id': r['candidate_id'], 'error': r['full_pool_mean_error'],
             'species_count': r['species_count'], 'reaction_count': r['reaction_count']}
            for r in reports if r['complete']]
    return {'objective': deepcopy(OBJECTIVE), 'candidates': reports,
            'common_panel': {'case_ids': common_ids, 'panel_id': digest(common_ids),
                             'count': len(common_ids), 'coverage': len(common_ids)/len(cases),
                             'points': points, 'provisional_front_ids': front(points),
                             'meaning': 'Observed common-panel tradeoffs only; inspect failures and missing groups.'},
            'full_pool_front_ids': front(full), 'confidence_interval': None}


def enrich_view(view, checkpoint, config):
    """Fail closed on stale state instead of silently merging inconsistent epochs."""
    view = deepcopy(view)
    budget = budget_report(checkpoint)
    checks = {
        'stage': checkpoint['stage'], 'parent_id': checkpoint['parent_id'],
        'remaining_cpu_seconds': budget['stage_remaining'],
        'search_grant_cpu_seconds': budget['search_remaining'],
        'raw_history_count': len(checkpoint['evidence']),
        'raw_archive_counts': {'evidence': len(checkpoint['evidence']),
            'actions': len(checkpoint['actions']), 'candidates': len(checkpoint['candidates'])}}
    for key, expected in checks.items():
        if key in view and view[key] != expected:
            raise ValueError('inconsistent snapshot field: '+key)
    if [c['id'] for c in view['candidate_details']] != checkpoint['active_ids']:
        raise ValueError('inconsistent active candidate snapshot')
    for candidate in view['candidate_details']:
        actual=checkpoint['candidates'][candidate['id']]
        for key in ('species_count','reaction_count','keep','multipliers'):
            if key in actual and candidate.get(key)!=actual[key]:
                raise ValueError('inconsistent candidate field: '+key)
    view.update(checks)
    view['budget'] = budget
    view['snapshot_id'] = digest(checkpoint)
    view['objective_feedback'] = evaluate(checkpoint['candidates'], checkpoint['parent_id'],
        config['cases'], checkpoint['evidence'], checkpoint['active_ids'])
    view['history_access'] = 'read_records(kind, candidate_id, case_id, operation, stage, offset, limit<=20) or read_evidence(artifact_id, offset, limit<=8000)'
    view['reviewed_rate_bounds'] = {rid: {key: row[key] for key in ('lower','upper','source')}
                                  for rid, row in config.get('rate_bounds', {}).items()}
    view['eligible_rate_ids'] = {mid: sorted(set(config.get('rate_bounds',{})) &
                                            set(checkpoint['candidates'][mid].get('reaction_ids',[])))
                                 for mid in checkpoint['active_ids']}
    by_family=defaultdict(list)
    cases={c['case_id']:c for c in config['cases']}
    for row in checkpoint['evidence']:
        if row.get('operation')!='query':continue
        cost=row.get('query_quote_cpu_seconds',row.get('logical_cpu_seconds'))
        if isinstance(cost,(int,float)) and math.isfinite(cost) and cost>=0:
            by_family[cases[row['case_id']]['operator']['family']].append(cost)
    view['observed_query_cost_by_family']={g:statistics(values) for g,values in sorted(by_family.items())}
    view['cost_estimate_boundary']='Observed logical CPU costs, including failed queries; descriptive, not a guaranteed future runtime.'
    return view


def read_records(checkpoint, kind='evidence', candidate_id=None, case_id=None,
                 operation=None, stage=None, offset=0, limit=20):
    if kind not in ('evidence', 'actions', 'candidates') or offset < 0 or not 1 <= limit <= 20:
        raise ValueError('kind evidence/actions/candidates; offset>=0, 1<=limit<=20')
    if candidate_id and candidate_id not in checkpoint['candidates']:
        raise PermissionError('candidate outside own history')
    rows = list(checkpoint['candidates'].values()) if kind == 'candidates' else checkpoint[kind]
    selected = []
    for row in rows:
        identities = {row.get('candidate_id'), row.get('id'),
                      row.get('payload', {}).get('candidate_id'), row.get('result', {}).get('candidate_id')}
        if candidate_id and candidate_id not in identities: continue
        if case_id and row.get('case_id') != case_id: continue
        if operation and row.get('operation') != operation: continue
        if stage is not None and row.get('stage') != stage: continue
        selected.append(row)
    end = min(len(selected), offset+limit)
    return {'rows': deepcopy(selected[offset:end]), 'offset': offset, 'total': len(selected),
            'next_offset': end if end < len(selected) else None,
            'snapshot_id': digest(checkpoint), 'read_only': True}
