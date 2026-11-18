"""Defined-denominator metrics for benchmark reports; never turn missing data into zero."""
import math


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def wilson(correct, total):
    """95% Wilson interval for independent binary cases (not correlated variants)."""
    if not total:
        return None
    z = 1.959963984540054
    p = correct / total
    scale = 1 + z*z / total
    center = (p + z*z/(2*total)) / scale
    radius = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / scale
    return [max(0, center-radius), min(1, center+radius)]


def distribution(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    def percentile(p):
        if not values:
            return None
        index = (len(values)-1) * p
        lo, hi = math.floor(index), math.ceil(index)
        return values[lo] + (values[hi]-values[lo]) * (index-lo)
    return dict(n=len(values), p50=percentile(.5), p90=percentile(.9), p95=percentile(.95),
                maximum=max(values) if values else None)


def aggregate(rows):
    """Rows contain applicable/executed, family_id, checks, TP/FP/FN, and actual usage.

    Every check is bool or None. None means unscored, not a successful rejection.
    Failed applicable executions must carry False for their required checks.
    """
    applicable = [r for r in rows if r.get('applicable', True)]
    executed = [r for r in applicable if r.get('executed', False)]
    completed = [r for r in executed if r.get('status') in ('ok', 'error')]
    successful = [r for r in completed if r.get('status') == 'ok']
    counts = dict(planned=len(rows), applicable=len(applicable), executed=len(executed),
                  completed=len(completed), pending=len(applicable)-len(completed),
                  families=len({r['family_id'] for r in applicable}), successful=len(successful))
    tp, fp, fn = (sum(r.get(k, 0) for r in completed) for k in ('tp', 'fp', 'fn'))
    checks = {}
    names = {k for r in applicable for k in r.get('checks', {})}
    for name in sorted(names):
        scored = [r for r in completed if type(r.get('checks', {}).get(name)) == bool]
        correct = sum(r['checks'][name] for r in scored)
        groups = {}
        for row in scored:
            groups.setdefault(row['family_id'], []).append(row['checks'][name])
        # Cluster variants/repeats before computing an interval; family passes only if all its scored members pass.
        family_correct = sum(all(values) for values in groups.values())
        checks[name] = dict(value=ratio(correct,len(scored)), numerator=correct, denominator=len(scored),
                            unscored=len(completed)-len(scored), familyPassRate=ratio(family_correct,len(groups)),
                            family95=wilson(family_correct,len(groups)), independentFamilies=len(groups))
    known_costs = [r['estimated_yuan'] for r in executed if type(r.get('estimated_yuan')) in (int,float)]
    input_values = [r['input_tokens'] for r in executed if type(r.get('input_tokens')) == int]
    output_values = [r['output_tokens'] for r in executed if type(r.get('output_tokens')) == int]
    return dict(coverage=counts, checks=checks,
                detection=dict(tp=tp,fp=fp,fn=fn,precision=ratio(tp,tp+fp),recall=ratio(tp,tp+fn),f1=ratio(2*tp,2*tp+fp+fn)),
                latencySeconds=distribution([r.get('seconds') for r in completed]),
                successfulLatencySeconds=distribution([r.get('seconds') for r in successful]),
                tokens=dict(input=sum(input_values),output=sum(output_values),
                            inputMissing=len(executed)-len(input_values),outputMissing=len(executed)-len(output_values)),
                cost=dict(knownEstimateYuan=sum(known_costs),unknownCases=len(executed)-len(known_costs),
                          estimatePerSuccessfulCase=ratio(sum(known_costs),len(successful)) if len(known_costs)==len(executed) else None,
                          basis='standard_price_upper_tier_estimate_not_cash_payment'))
