"""Opt-in reclaim of unused batch budget after explicit terminal censoring.

Never retries transport uncertainty, worker errors or chemistry failures. Every
attempt is yielded and charged; a successful first attempt is never repeated.
"""
import math


def weighted_grants(cases,ids,budget):
    """One allocation implementation for execution AND recovery feasibility."""
    if not ids or len(ids)!=len(set(ids)) or not math.isfinite(budget) or budget<=0:
        raise ValueError('positive finite budget and distinct case IDs required')
    weights=[1. if cases[cid]['operator']['family']=='ignition_delay' else 180. for cid in ids]
    return [budget*weight/sum(weights) for weight in weights]


class BudgetRecoveryBackend:
    def __init__(self, backend, policy):
        if set(policy)!={'max_retries'} or type(policy['max_retries']) is not int or policy['max_retries']!=1:
            raise ValueError('only one explicit terminal budget-censoring retry is supported')
        self.backend=backend
        if not callable(getattr(backend,'batch_grants',None)):
            raise ValueError('budget recovery requires authoritative per-case grant quotes')
        self.workers=getattr(backend,'workers',1)
        self.policy=dict(policy)

    def execute_batch(self,candidate,ids,operation,targets,max_cpu_seconds):
        if not ids or len(ids)!=len(set(ids)) or not math.isfinite(max_cpu_seconds) or max_cpu_seconds<=0:
            raise ValueError('distinct case IDs and positive finite batch budget required')
        pending=list(ids);spent=0.;attempt=0
        while pending:
            remaining=max_cpu_seconds-spent
            if remaining<=.01:return
            grants=dict(zip(pending,self.backend.batch_grants(pending,remaining)))
            completed={}
            # Normal generator exhaustion is required before another request.
            # A partial generator error propagates, preserving uncertain-job rules.
            for original in self.backend.execute_batch(candidate,pending,operation,targets,remaining):
                row=dict(original);cid=row['case_id']
                if cid not in pending or cid in completed or row['candidate_id']!=candidate['id']:
                    raise ValueError('budget recovery response identity mismatch')
                cost=row['logical_cpu_seconds']
                if type(cost) not in (int,float) or not math.isfinite(cost) or cost<0:
                    raise ValueError('invalid charged cost in terminal budget recovery')
                spent+=cost;completed[cid]=row
                row['budget_recovery']=dict(attempt=attempt,original_batch_grant=max_cpu_seconds,
                    case_cpu_grant=grants[cid],
                    cumulative_batch_logical_cpu=spent,
                    reason='original request' if attempt==0 else 'terminal budget_censored; unused original grant reclaimed')
                yield row
            if set(completed)!=set(pending):
                raise RuntimeError('incomplete batch is uncertain; no resource retry')
            if attempt>=self.policy['max_retries']:return
            pending=[cid for cid in pending if completed[cid]['status']=='budget_censored']
            if pending and max_cpu_seconds-spent>.01:
                proposed=self.backend.batch_grants(pending,max_cpu_seconds-spent)
                # A fresh solve cannot reasonably recover a CPU-limited run with
                # an equal or SMALLER allowance. Do not burn the last budget on
                # retries dominated by the failed attempt's observed lower bound.
                pending=[cid for cid,grant in zip(pending,proposed)
                         if grant>max(grants[cid],completed[cid]['logical_cpu_seconds'])]
            attempt+=1

    def execute(self,candidate,case_id,operation,targets,max_cpu_seconds):
        # The shared workspace calls execute for a single case and expects ONE
        # row. Preserve that contract rather than hiding retry costs in one row.
        return self.backend.execute(candidate,case_id,operation,targets,max_cpu_seconds)
