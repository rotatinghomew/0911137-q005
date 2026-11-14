"""竞争风险下的备件风险估计。

口径（与 docs/data-contract.md 对齐）：

- 时间尺度使用累计运行小时（operating age），业务日历中的停机间隙不计入暴露；
  intervals 为半开区间 [start_hour,end_hour)，按并集去重。
- failure 与 retire 是竞争结局：报废之后不再产生暴露，也不会再发生故障；
  inspection（计划检修）只是事件，不是故障，也不改变年龄。
- 维修等级按现有资料解释：overhaul（大修）视为年龄重置，开启新的寿命片段；
  minor（小修）不重置年龄。维修后没有观测到任何故障，因此维修后片段的故障率
  无法从资料中识别，只能给出零事件上界，绝不外推成精确风险。
- 只使用 known_hour <= cutoff 的记录，保证历史时点视图可复现。
"""

from collections import defaultdict
from math import comb, exp

from .statistics import poisson_quantile, poisson_rate_interval

FAILURE = 'failure'
RETIRE = 'retire'
OVERHAUL = 'overhaul'
# 超出已观测故障年龄后的外推没有资料支持，统一使用该状态标记而不是编造精确值。
BEYOND_OBSERVED = 'beyond_observed_age'
INSUFFICIENT = 'insufficient_post_repair_evidence'
ESTIMATED = 'estimated_within_observed_age'


def merged_intervals(rows, limit):
    """返回业务时标 [0,limit) 内可见运行区间的并集。"""
    bounds = []
    for row in rows:
        if row['known_hour'] > limit or row['start_hour'] >= limit:
            continue
        end = row['end_hour'] if row['end_hour'] is not None else limit
        end = min(end, limit)
        if end < row['start_hour']:
            raise ValueError('运行区间倒置')
        bounds.append((row['start_hour'], end))
    merged = []
    for start, end in sorted(bounds):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def operating_age(merged, business_hour):
    """业务时标点对应的累计运行小时。"""
    return sum(max(0.0, min(end, business_hour) - start)
               for start, end in merged if start < business_hour)


def visible(rows, cutoff, hour_field='hour'):
    return [row for row in rows if row['known_hour'] <= cutoff and row[hour_field] <= cutoff]


def build_episodes(tables, cutoff):
    """把每台设备拆成可用于风险集的寿命片段。

    大修开启新片段且年龄归零；小修保留在同一片段内，仅作为记录点。
    片段结尾为故障、报废竞争删失，或当前分析时点的右删失。
    """
    machines = [m for m in tables['machines'] if m['installed_hour'] <= cutoff]
    intervals_by_machine = defaultdict(list)
    for row in tables['intervals']:
        intervals_by_machine[row['machine']].append(row)
    events_by_machine = defaultdict(list)
    for row in visible(tables['events'], cutoff):
        events_by_machine[row['machine']].append(row)
    repairs_by_machine = defaultdict(list)
    for row in visible(tables['repairs'], cutoff):
        repairs_by_machine[row['machine']].append(row)

    episodes = []
    for machine in machines:
        mid = machine['machine']
        events = sorted(events_by_machine[mid], key=lambda row: row['hour'])
        repairs = sorted(repairs_by_machine[mid], key=lambda row: row['hour'])
        retired_hour = min((row['hour'] for row in events if row['kind'] == RETIRE), default=None)
        limit = min(cutoff, retired_hour) if retired_hour is not None else cutoff
        merged = merged_intervals(intervals_by_machine[mid], limit)

        # 每个大修切一刀；小修不切片段。切分点按业务时标转成运行年龄。
        overhaul_hours = [row['hour'] for row in repairs if row['grade'] == OVERHAUL and row['hour'] <= limit]
        segment_breaks = [0] + overhaul_hours + [limit]
        minor_hours = [row['hour'] for row in repairs if row['grade'] != OVERHAUL and row['hour'] <= limit]

        for index in range(len(segment_breaks) - 1):
            seg_start, seg_end = segment_breaks[index], segment_breaks[index + 1]
            start_age = operating_age(merged, seg_start)
            end_age = operating_age(merged, seg_end)
            # 片段内只接受严格晚于片段起点的事件；大修已把片段切开，
            # 小修不切片段，故故障属于它实际发生的那一段。
            segment_events = [row for row in events
                              if seg_start < row['hour'] <= seg_end and row['kind'] in (FAILURE, RETIRE)]
            failure_events = [row for row in segment_events if row['kind'] == FAILURE]
            retire_events = [row for row in segment_events if row['kind'] == RETIRE]
            ending = 'censored'
            event_hour = None
            # 报废是竞争结局且不可恢复，优先作为片段终点；故障只有在片段内未被
            # 后续维修（小修）接续运行时才终止片段，否则设备继续留在风险集中。
            if retire_events:
                ending = RETIRE
                event_hour = min(row['hour'] for row in retire_events)
            elif failure_events and not any(seg_start < hour <= seg_end for hour in minor_hours):
                ending = FAILURE
                event_hour = min(row['hour'] for row in failure_events)
            segment_minor = [hour for hour in minor_hours if seg_start < hour <= seg_end]
            # 片段局部年龄：大修重置后从 0 重新计龄，便于在同一时间尺度上进入风险集。
            local_failure = (operating_age(merged, event_hour) - start_age
                             if ending == FAILURE else None)
            local_retire = (operating_age(merged, event_hour) - start_age
                            if ending == RETIRE else None)
            # 未终止片段中的复发故障同样要进入风险集，按局部年龄记录。
            all_failure_ages = [operating_age(merged, row['hour']) - start_age
                                for row in failure_events]
            episodes.append({
                'machine': mid,
                'site': machine['site'],
                'batch': machine['batch'],
                'episode': index,
                'post_overhaul': index > 0,
                'start_age': start_age,
                'end_age': end_age,
                'age_length': end_age - start_age,
                'ending': ending,
                'event_hour': event_hour,
                'failure_age': local_failure,
                'failure_ages': all_failure_ages,
                'retire_age': local_retire,
                'failures_within_episode': len(failure_events),
                'minor_repairs': len(segment_minor),
                'retired_at_cutoff': retired_hour is not None and retired_hour <= cutoff,
            })
    return episodes


def risk_table(episodes):
    """按运行年龄构造风险集表（故障与报废竞争）。

    故障是可复发事件：小修后片段不终止，但每次故障仍按其局部运行年龄计入事件；
    报废是竞争结局，在报废年龄离开风险集。每个片段使用大修重置后的局部年龄。
    每行给出该年龄仍在风险中的片段数、故障数、报废数与 Nelson-Aalen 增量。
    """
    failure_points = sorted(age for ep in episodes for age in ep.get('failure_ages', [])
                            if age is not None)
    retire_points = sorted(ep['retire_age'] for ep in episodes
                           if ep['ending'] == RETIRE and ep['retire_age'] is not None)
    grid = sorted(set(failure_points) | set(retire_points))
    table = []
    for age in grid:
        # 局部年龄落在 [0, 片段长度] 内的片段仍在风险集中。
        at_risk = sum(1 for ep in episodes if age <= ep['end_age'] - ep['start_age'] + 1e-9)
        failures = sum(1 for point in failure_points if abs(point - age) < 1e-9)
        retires = sum(1 for point in retire_points if abs(point - age) < 1e-9)
        increment = failures / at_risk if at_risk else 0.0
        table.append({'age': age, 'at_risk': at_risk, 'failures': failures,
                      'retires': retires, 'hazard_increment': increment})
    cumulative = 0.0
    for row in table:
        cumulative += row['hazard_increment']
        row['nelson_aalen'] = cumulative
    return table


def rate_estimate(episodes, confidence=0.95):
    """Poisson 故障率 MLE 与精确区间。

    故障是可复发事件（小修后设备重新上线），分子按片段内故障事件数
    failures_within_episode 计，而不是只统计以故障结尾的片段；
    暴露为各片段运行小时之和。
    """
    exposure = sum(ep['age_length'] for ep in episodes)
    failures = sum(ep.get('failures_within_episode', 1 if ep['ending'] == FAILURE else 0)
                   for ep in episodes)
    if exposure <= 0:
        return {'exposure': 0.0, 'failures': failures, 'rate': None,
                'rate_lower': None, 'rate_upper': None, 'confidence': confidence}
    lower, upper = poisson_rate_interval(failures, exposure, confidence)
    return {'exposure': exposure, 'failures': failures,
            'rate': failures / exposure, 'rate_lower': lower, 'rate_upper': upper,
            'confidence': confidence}


def homogeneity_evidence(episodes):
    """跨站点等率检验：给定总故障数下的条件二项单侧检验。

    站点1 集中了全部故障。在共同率假设下，各站点故障数按暴露占比服从多项分布；
    站点1 至少出现观测故障数的概率即二项尾部。同时报告批次与负荷的可比性证据。
    """
    exposure_by_site = defaultdict(float)
    failures_by_site = defaultdict(int)
    batches_by_site = defaultdict(set)
    for ep in episodes:
        exposure_by_site[ep['site']] += ep['age_length']
        batches_by_site[ep['site']].add(ep['batch'])
        failures_by_site[ep['site']] += ep.get(
            'failures_within_episode', 1 if ep['ending'] == FAILURE else 0)
    total_exposure = sum(exposure_by_site.values())
    total_failures = sum(failures_by_site.values())
    site = max(failures_by_site, key=lambda key: failures_by_site[key]) if total_failures else None
    p_value = None
    expected = {}
    if site is not None and total_exposure > 0:
        share = exposure_by_site[site] / total_exposure
        expected = {key: total_failures * exposure_by_site[key] / total_exposure
                    for key in exposure_by_site}
        if 0 < share < 1:
            # 条件二项尾部：共同率假设下该站点至少出现观测故障数的概率。
            observed = failures_by_site[site]
            p_value = sum(comb(total_failures, j) * (share ** j) *
                          ((1 - share) ** (total_failures - j))
                          for j in range(observed, total_failures + 1))
    return {
        'sites': sorted(exposure_by_site),
        'exposure_by_site': dict(exposure_by_site),
        'failures_by_site': dict(failures_by_site),
        'expected_failures_if_common_rate': expected,
        'concentration_site': site,
        'conditional_binomial_p_value': p_value,
        'batches_per_site': {key: sorted(value) for key, value in batches_by_site.items()},
    }


def kaplan_meier(table):
    """乘积极限生存函数；竞争风险下把报废作为离开风险集的删失。"""
    survival = 1.0
    rows = []
    for row in table:
        if row['at_risk']:
            survival *= 1 - row['failures'] / row['at_risk']
        rows.append({**row, 'kaplan_meier': survival})
    return rows


def conditional_failure_probability(current_age, horizon, rate_info, max_failure_age):
    """单台设备在给定运行年龄上、未来 horizon 运行小时内的故障概率。

    返回两个层次，避免把外推伪装成精确风险：

    - strict：只在窗口完全落在已观测故障年龄内时给概率，否则 probability 为 None
      并标记 beyond_observed_age；
    - extrapolation：恒定故障率（指数）模型在窗口上的条件概率，无论是否超龄都计算，
      但用 extrapolated 标记说明该部分没有故障观测支持，区间来自率的精确 Poisson 区间。
    """
    rate = rate_info['rate']
    if rate is None:
        return {'status': INSUFFICIENT, 'probability': None, 'lower': None, 'upper': None,
                'strict_probability': None, 'extrapolation': None,
                'reason': '该分组没有可用暴露或故障识别不出故障率'}

    covered = min(horizon, max(0.0, max_failure_age - current_age))
    extrapolated_hours = max(0.0, horizon - covered)

    def prob(r, hours):
        if hours <= 0 or r is None:
            return None
        return 1 - exp(-r * hours)

    extrapolation = {
        'model': 'constant_hazard_exponential',
        'rate_source': 'site_specific_first_life_constant_rate',
        'probability': prob(rate, horizon),
        'lower': prob(rate_info['rate_lower'], horizon),
        'upper': prob(rate_info['rate_upper'], horizon),
        'extrapolated_hours': extrapolated_hours,
        'evidence_supported_hours': covered,
        'label': '窗口内概率基于恒定故障率模型，超龄部分无故障观测支持',
    }

    if current_age > max_failure_age + 1e-9 or covered <= 0:
        return {'status': BEYOND_OBSERVED, 'probability': None, 'lower': None, 'upper': None,
                'strict_probability': None, 'extrapolation': extrapolation,
                'reason': f'当前运行年龄 {round_smart(current_age)} 已超过最大观测故障年龄 {round_smart(max_failure_age)}，严格口径不给点估计'}

    point = prob(rate, covered)
    lower = prob(rate_info['rate_lower'], covered)
    upper = prob(rate_info['rate_upper'], covered)
    truncated = covered < horizon
    return {'status': ESTIMATED, 'probability': point, 'lower': lower, 'upper': upper,
            'strict_probability': point, 'extrapolation': extrapolation,
            'covered_horizon_hours': covered,
            'truncated_reason': '窗口超出已观测故障年龄的部分不外推' if truncated else None}


def round_smart(value, digits=6):
    if value is None:
        return None
    rounded = round(float(value), digits)
    return int(rounded) if rounded == int(rounded) else rounded


def fleet_forecast(machine_forecasts, confidence=0.95):
    """把单机概率聚合成备件需求的 Poisson 预测区间。

    分别汇总严格证据口径（strict_expected，超龄机器不贡献点估计）与恒定率
    模型外推口径（model_expected，所有非维修后机器贡献，但带外推标记），
    并给出率区间上界对应的保守需求，便于班组解释备货范围。
    """
    strict_expected = 0.0
    model_expected = 0.0
    model_upper = 0.0
    post_repair_upper = 0.0
    estimable = 0
    extrapolated = 0
    insufficient = 0
    for row in machine_forecasts:
        status = row.get('status')
        if status == 'retired':
            continue
        if status == INSUFFICIENT:
            insufficient += 1
            if row.get('upper') is not None:
                post_repair_upper += row['upper']
            continue
        if row.get('strict_probability') is not None:
            strict_expected += row['strict_probability']
            estimable += 1
        else:
            extrapolated += 1
        model = row.get('extrapolation')
        if model and model.get('probability') is not None:
            model_expected += model['probability']
            if model.get('upper') is not None:
                model_upper += model['upper']
    alpha = 1 - confidence
    interval = None
    if model_expected > 0:
        interval = [
            poisson_quantile(alpha / 2, model_expected),
            poisson_quantile(1 - alpha / 2, model_expected),
        ]
    conservative = None
    total_upper = model_upper + post_repair_upper
    if total_upper > 0:
        conservative = poisson_quantile(1 - alpha / 2, total_upper)
    strict_interval = None
    if strict_expected > 0:
        strict_interval = [
            poisson_quantile(alpha / 2, strict_expected),
            poisson_quantile(1 - alpha / 2, strict_expected),
        ]
    return {
        'active_machines_estimable_within_observed_age': estimable,
        'active_machines_beyond_observed_age': extrapolated,
        'active_machines_post_repair_unidentified': insufficient,
        'strict_expected_failures': round(strict_expected, 6),
        'strict_poisson_interval': strict_interval,
        'model_expected_failures': round(model_expected, 6),
        'model_expected_upper_rate_sum': round(model_upper, 6),
        'model_poisson_prediction_interval': interval,
        'post_repair_upper_probability_sum': round(post_repair_upper, 6),
        'conservative_upper_need': conservative,
        'confidence': confidence,
    }
