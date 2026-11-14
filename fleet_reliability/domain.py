import math

from .reliability import (
    BEYOND_OBSERVED,
    INSUFFICIENT,
    build_episodes,
    conditional_failure_probability,
    fleet_forecast,
    homogeneity_evidence,
    kaplan_meier,
    merged_intervals,
    operating_age,
    rate_estimate,
    risk_table,
    visible,
)
from .statistics import poisson_rate_interval


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('数值必须为有限实数')
    return value


def selected(value, group):
    return not group or value == group


def latest(rows, key, revision, visible, cutoff):
    result = {}
    for row in rows:
        if row[visible] > cutoff:
            continue
        previous = result.get(row[key])
        if previous is None or row[revision] > previous[revision]:
            result[row[key]] = row
    return list(result.values())


def output(rows, summary, notes):
    return {'rows': rows, 'summary': summary, 'notes': notes}


def exposure(intervals, cutoff):
    bounds = sorted((row['start_hour'], min(row['end_hour'] if row['end_hour'] is not None else cutoff, cutoff)) for row in intervals if row['known_hour'] <= cutoff and row['start_hour'] < cutoff)
    merged = []
    for start, end in bounds:
        if end < start:
            raise ValueError('运行区间倒置')
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def run(data, options):
    cutoff, group = options['cutoff'], options['group']
    rows = []
    for machine in data['machines']:
        if machine['installed_hour'] > cutoff or not selected(machine['site'], group):
            continue
        events = [row for row in data['events'] if row['machine'] == machine['machine'] and row['hour'] <= cutoff and row['known_hour'] <= cutoff]
        retired = min((row['hour'] for row in events if row['kind'] == 'retire'), default=None)
        limit = min(cutoff, retired) if retired is not None else cutoff
        intervals = [row for row in data['intervals'] if row['machine'] == machine['machine']]
        hours = exposure(intervals, limit)
        failures = sum(row['kind'] == 'failure' and row['hour'] <= limit for row in events)
        repairs = [row for row in data['repairs'] if row['machine'] == machine['machine'] and row['known_hour'] <= cutoff and row['hour'] <= cutoff]
        rows.append(dict(machine=machine['machine'], site=machine['site'], batch=machine['batch'], operating_hours=hours, failures=failures, inspections=sum(row['kind'] == 'inspection' for row in events), retired=retired is not None, repairs=len(repairs), overhauls=sum(row['grade'] == 'overhaul' for row in repairs)))
    return output(rows, {'设备数': len(rows), '故障数': sum(row['failures'] for row in rows), '运行小时': sum(row['operating_hours'] for row in rows)}, ['历史事件统计不等于未来故障风险。'])


# 风险分析口径提示，页面与保存版本共用同一份文字，避免口径漂移。
RISK_NOTES = [
    '时间尺度为累计运行小时：业务日历中的停机间隙不计入暴露，运行区间按并集去重。',
    '故障与报废是竞争结局：报废设备在报废时离开风险集，之后不再计暴露；计划检修（inspection）不是故障，不改变年龄。',
    '大修（overhaul）按现有维修等级解释为年龄重置、开启新寿命片段；小修（minor）不重置年龄。该解释随结果保存，维修后零故障使重置效果无法被资料直接验证。',
    '维修后片段未观测到任何故障，故障率不可识别，只给零事件精确上界，不外推为精确风险。',
    '在用设备当前运行年龄普遍超过最大观测故障年龄，超出部分不给点估计，状态标记为 beyond_observed_age。',
    '跨站点借用必须通过等率检验；站点2、站点3 零故障，借用站点1 点估计没有证据，仅保留各自零事件上界与显式情景。',
    '备件需求为未来运行小时窗口内的期望故障数与 Poisson 预测区间，不是确定订单量；业务日历换算由班组按实际排班确定。',
]

DEFAULT_HORIZON_HOURS = 720
RISK_CONFIDENCE = 0.95


def _machine_current_state(tables, machine, cutoff):
    """设备在 cutoff 时点的当前状态与当前寿命片段年龄。"""
    mid = machine['machine']
    events = visible([row for row in tables['events'] if row['machine'] == mid], cutoff)
    repairs = visible([row for row in tables['repairs'] if row['machine'] == mid], cutoff)
    retired = min((row['hour'] for row in events if row['kind'] == 'retire'), default=None)
    limit = min(cutoff, retired) if retired is not None else cutoff
    merged = merged_intervals([row for row in tables['intervals'] if row['machine'] == mid], limit)
    current_age = sum(end - start for start, end in merged)
    overhauls = [row for row in repairs if row['grade'] == 'overhaul' and row['hour'] <= limit]
    last_overhaul = max((row['hour'] for row in overhauls), default=None)
    # 当前片段年龄：若最近一次大修在限界内，从大修时重新计龄。
    if last_overhaul is not None and last_overhaul <= limit:
        episode_age = current_age - operating_age(merged, last_overhaul)
        post_overhaul = True
    else:
        episode_age = current_age
        post_overhaul = False
    failures = sum(row['kind'] == 'failure' for row in events if row['hour'] <= limit)
    return {
        'machine': mid,
        'site': machine['site'],
        'batch': machine['batch'],
        'status': 'retired' if retired is not None else 'active',
        'retired_hour': retired,
        'operating_hours': current_age,
        'current_episode_age': episode_age,
        'post_overhaul': post_overhaul,
        'failures_observed': failures,
        'minor_repairs': sum(row['grade'] != 'overhaul' for row in repairs),
        'overhauls': len(overhauls),
        'visible_events': [list(row) for row in sorted((row['hour'], row['kind']) for row in events)],
        'visible_repairs': [list(row) for row in sorted((row['hour'], row['grade']) for row in repairs)],
    }


def risk_analysis(tables, options):
    """生成可验证的风险估计与备件需求范围。

    options: cutoff（业务时标）、group（站点或空）、horizon_hours（预测窗口，运行小时）。
    """
    cutoff = options['cutoff']
    group = options.get('group', '')
    horizon = int(options.get('horizon_hours', DEFAULT_HORIZON_HOURS))
    if horizon <= 0:
        raise ValueError('预测窗口须为正整数运行小时')

    episodes_all = build_episodes(tables, cutoff)
    in_scope = [ep for ep in episodes_all if not group or ep['site'] == group]

    # 首段（未经大修重置）用于识别基础故障率；维修后片段单独处理。
    first_life = [ep for ep in in_scope if not ep['post_overhaul']]
    post_repair = [ep for ep in in_scope if ep['post_overhaul']]

    max_failure_age = max((age for ep in first_life for age in ep.get('failure_ages', [])
                           if age is not None), default=None)
    failure_ages = sorted({age for ep in first_life for age in ep.get('failure_ages', [])
                           if age is not None})

    # 分站点率估计（全部站点都算，借用判断才有证据）。
    site_rates = {}
    for site in sorted({ep['site'] for ep in episodes_all}):
        site_first = [ep for ep in episodes_all if ep['site'] == site and not ep['post_overhaul']]
        site_rates[site] = rate_estimate(site_first, RISK_CONFIDENCE)

    pooled_first = rate_estimate(first_life, RISK_CONFIDENCE)
    post_rate = rate_estimate(post_repair, RISK_CONFIDENCE)
    # 维修后零故障：直接给零事件上界，rate 点估计保持 0 但标记不可识别。
    post_repair_bound = None
    if post_rate['exposure'] > 0 and post_rate['failures'] == 0:
        _, upper = poisson_rate_interval(0, post_rate['exposure'], RISK_CONFIDENCE)
        post_repair_bound = {'exposure': post_rate['exposure'], 'failures': 0,
                             'rate_upper_95': upper, 'episodes': len(post_repair),
                             'status': INSUFFICIENT}

    table = kaplan_meier(risk_table(first_life)) if first_life else []
    if table:
        table = [{'method_note': '描述性首次事件分析：小修后复发故障未在 KM 中重复计险，复发故障率以 site_rates 的 Poisson 估计为准', **row}
                 if index == 0 else row for index, row in enumerate(table)]
    # 借用证据与全网故障年龄始终基于全部站点，分组视图也必须保留借用判断依据。
    evidence = homogeneity_evidence(episodes_all)
    fleet_failure_ages = sorted({age for ep in episodes_all
                                 for age in ep.get('failure_ages', []) if age is not None})
    evidence['max_failure_age'] = fleet_failure_ages[-1] if fleet_failure_ages else None
    evidence['failure_ages'] = fleet_failure_ages
    evidence['pooling_supported'] = (
        evidence['conditional_binomial_p_value'] is not None
        and evidence['conditional_binomial_p_value'] >= 0.05
    )

    machines = [m for m in tables['machines']
                if m['installed_hour'] <= cutoff and (not group or m['site'] == group)]
    machine_rows = []
    for machine in machines:
        state = _machine_current_state(tables, machine, cutoff)
        site_rate = site_rates.get(machine['site'], pooled_first)
        forecast = {'status': 'retired', 'probability': None, 'lower': None, 'upper': None,
                    'strict_probability': None, 'extrapolation': None,
                    'reason': '设备已报废，不进入未来需求'}
        if state['status'] == 'active':
            if state['post_overhaul']:
                if post_repair_bound is not None:
                    # 维修后：只给基于零事件上界率的保守概率，明确不是点估计。
                    upper_prob = 1 - math.exp(-post_repair_bound['rate_upper_95'] * horizon)
                    forecast = {'status': INSUFFICIENT, 'probability': None, 'lower': 0.0,
                                'upper': upper_prob, 'strict_probability': None,
                                'extrapolation': None,
                                'reason': '大修后片段零故障，仅能给 95% 上界概率，不能识别点估计'}
                else:
                    forecast = {'status': INSUFFICIENT, 'probability': None, 'lower': None,
                                'upper': None, 'strict_probability': None,
                                'extrapolation': None, 'reason': '没有维修后观测暴露'}
            elif max_failure_age is None:
                # 该分组自身零故障：率 MLE 为零，严格口径无点估计，模型层给零事件上界。
                forecast = {'status': BEYOND_OBSERVED, 'probability': None, 'lower': None,
                            'upper': None, 'strict_probability': None,
                            'extrapolation': {
                                'model': 'constant_hazard_exponential',
                                'rate_source': 'site_specific_zero_failure_bound',
                                'probability': 0.0,
                                'lower': 0.0,
                                'upper': (1 - math.exp(-site_rate['rate_upper'] * horizon)
                                          if site_rate['rate_upper'] is not None else None),
                                'extrapolated_hours': float(horizon),
                                'evidence_supported_hours': 0.0,
                                'label': '该站点零故障，点估计不可识别，仅给零事件 95% 上界',
                            },
                            'reason': '该站点没有任何故障观测，故障率不可识别，只保留零事件上界'}
            else:
                forecast = conditional_failure_probability(
                    state['current_episode_age'], horizon, site_rate, max_failure_age)
        machine_rows.append({**state, 'forecast': forecast})

    active_rows = [row for row in machine_rows if row['status'] == 'active']
    fleet = fleet_forecast([row['forecast'] for row in active_rows], RISK_CONFIDENCE)

    # 显式情景：站点自身证据 vs 借用全网首段率（仅在等率检验支持时才允许作为主口径）。
    fleet_pooled = rate_estimate(
        [ep for ep in episodes_all if not ep['post_overhaul']], RISK_CONFIDENCE)
    scenarios = _spare_scenarios(machine_rows, site_rates, fleet_pooled, post_repair_bound,
                                 max_failure_age, horizon, evidence)

    return {
        'risk': {
            'cutoff': cutoff,
            'group': group,
            'horizon_hours': horizon,
            'confidence': RISK_CONFIDENCE,
            'episodes': in_scope,
            'risk_table': table,
            'site_rates': site_rates,
            'pooled_first_life_rate': pooled_first,
            'post_repair': post_repair_bound,
            'homogeneity': evidence,
            'machines': machine_rows,
            'fleet_forecast': fleet,
            'spare_part_scenarios': scenarios,
            'notes': list(RISK_NOTES),
        }
    }


def _spare_scenarios(machine_rows, site_rates, pooled, post_bound, max_failure_age, horizon, evidence):
    """构造班组可解释的备件情景，严格口径与模型外推分开、借用关系显式标注。"""
    def run_scenario(label, use_pooled):
        strict_expected = 0.0
        model_expected = 0.0
        model_upper = 0.0
        post_upper = 0.0
        within_age = 0
        beyond_age = 0
        post_repair_machines = 0
        for row in machine_rows:
            if row['status'] != 'active':
                continue
            if row['post_overhaul']:
                post_repair_machines += 1
                if post_bound is not None:
                    post_upper += 1 - math.exp(-post_bound['rate_upper_95'] * horizon)
                continue
            info = pooled if use_pooled else site_rates[row['site']]
            age = row['current_episode_age']
            if info['rate'] is None:
                beyond_age += 1
                continue
            # 严格口径只认分组自身观测到的故障年龄；借用情景使用全网故障年龄。
            strict_max_age = max_failure_age
            model_max_age = evidence['max_failure_age'] if use_pooled else max_failure_age
            if model_max_age is None:
                # 全网都没有故障观测：只剩零事件上界可累计。
                beyond_age += 1
                if info['rate_upper'] is not None:
                    model_upper += 1 - math.exp(-info['rate_upper'] * horizon)
                continue
            model_expected += 1 - math.exp(-info['rate'] * horizon)
            model_upper += 1 - math.exp(-info['rate_upper'] * horizon)
            covered = min(horizon, max(0.0, strict_max_age - age)) if strict_max_age is not None else 0
            if covered > 0:
                strict_expected += 1 - math.exp(-info['rate'] * covered)
                within_age += 1
            else:
                beyond_age += 1
        return {
            'label': label,
            'machines_within_observed_age': within_age,
            'machines_beyond_observed_age': beyond_age,
            'post_repair_machines_upper_bound_only': post_repair_machines,
            'strict_expected_failures': round(strict_expected, 6),
            'model_expected_failures': round(model_expected, 6),
            'model_upper_rate_expected': round(model_upper, 6),
            'post_repair_upper_expected': round(post_upper, 6),
            'conservative_expected_sum': round(model_upper + post_upper, 6),
        }

    site_scenario = run_scenario('各站点自身证据：零故障站点率 MLE 为零，只保留零事件 95% 上界', False)
    pooled_scenario = run_scenario('借用全网首段故障率：等率检验不支持，仅作敏感性对照而非主口径', True)
    return {
        'pooling_supported': evidence['pooling_supported'],
        'conditional_binomial_p_value': evidence['conditional_binomial_p_value'],
        'site_specific': site_scenario,
        'pooled_sensitivity': pooled_scenario,
        'recommendation': 'site_specific' if not evidence['pooling_supported'] else 'pooled_sensitivity',
    }
