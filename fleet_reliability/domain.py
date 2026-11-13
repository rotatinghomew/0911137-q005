from collections import Counter, defaultdict
import math
from statistics import mean, median


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
