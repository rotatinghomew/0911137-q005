import unittest

from fleet_reliability.reliability import (
    BEYOND_OBSERVED,
    INSUFFICIENT,
    build_episodes,
    homogeneity_evidence,
    merged_intervals,
    operating_age,
    risk_table,
)


def machine(mid, site='甲', batch='b', installed=0):
    return {'machine': mid, 'site': site, 'batch': batch, 'installed_hour': installed}


def interval(mid, start, end, known=None, load=1.0):
    return {'machine': mid, 'start_hour': start, 'end_hour': end,
            'known_hour': start if known is None else known, 'load': load}


def event(mid, hour, kind, known=None):
    return {'event': f'{mid}-{kind}', 'machine': mid, 'hour': hour,
            'known_hour': hour if known is None else known, 'kind': kind}


def repair(mid, hour, grade, known=None):
    return {'machine': mid, 'hour': hour, 'known_hour': hour if known is None else known,
            'grade': grade}


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            'machines': [machine('a'), machine('b', site='乙')],
            'intervals': [interval('a', 0, 100), interval('b', 0, 100)],
            'events': [event('a', 60, 'failure'), event('a', 90, 'retire')],
            'repairs': [],
        }

    def test_competing_retirement_cuts_exposure(self):
        episodes = build_episodes(self.tables, 200)
        a = next(ep for ep in episodes if ep['machine'] == 'a')
        self.assertEqual(a['ending'], 'retire')
        # 报废在业务时标 90，区间连续，运行年龄即 90
        self.assertEqual(a['retire_age'], 90)
        self.assertEqual(a['age_length'], 90)

    def test_inspection_is_not_failure_and_not_ending(self):
        self.tables['events'].append(event('b', 50, 'inspection'))
        episodes = build_episodes(self.tables, 200)
        b = next(ep for ep in episodes if ep['machine'] == 'b')
        self.assertEqual(b['ending'], 'censored')
        self.assertEqual(b['failures_within_episode'], 0)

    def test_overhaul_resets_age_and_opens_new_episode(self):
        self.tables['machines'] = [machine('a')]
        self.tables['events'] = [event('a', 40, 'failure')]
        self.tables['repairs'] = [repair('a', 50, 'overhaul')]
        episodes = build_episodes(self.tables, 200)
        self.assertEqual(len(episodes), 2)
        first, second = episodes
        self.assertFalse(first['post_overhaul'])
        self.assertEqual(first['ending'], 'failure')
        self.assertTrue(second['post_overhaul'])
        # 第二段年龄从 0 重新计：局部终点年龄 = 100 - 大修时年龄 50
        self.assertEqual(second['end_age'] - second['start_age'], 50)
        self.assertEqual(second['ending'], 'censored')

    def test_minor_repair_keeps_single_episode_and_failure_recurs(self):
        self.tables['machines'] = [machine('a')]
        self.tables['events'] = [event('a', 40, 'failure')]
        self.tables['repairs'] = [repair('a', 50, 'minor')]
        episodes = build_episodes(self.tables, 200)
        self.assertEqual(len(episodes), 1)
        only = episodes[0]
        self.assertEqual(only['ending'], 'censored')
        self.assertEqual(only['failures_within_episode'], 1)
        self.assertEqual(only['minor_repairs'], 1)
        self.assertEqual(only['failure_ages'], [40])

    def test_unknown_records_are_hidden_at_cutoff(self):
        self.tables['events'] = [event('a', 60, 'failure', known=120),
                                 event('a', 90, 'retire', known=120)]
        episodes = build_episodes(self.tables, 100)
        a = next(ep for ep in episodes if ep['machine'] == 'a')
        self.assertEqual(a['ending'], 'censored')
        self.assertEqual(a['failures_within_episode'], 0)

    def test_gaps_in_intervals_do_not_accumulate_exposure(self):
        tables = {
            'machines': [machine('a')],
            'intervals': [interval('a', 0, 40), interval('a', 60, 100)],
            'events': [],
            'repairs': [],
        }
        episodes = build_episodes(tables, 200)
        self.assertEqual(episodes[0]['age_length'], 80)
        self.assertEqual(operating_age(merged_intervals(tables['intervals'], 200), 80), 60)

    def test_risk_table_counts_recurrent_failures_and_retirements(self):
        tables = {
            'machines': [machine('a'), machine('b')],
            'intervals': [interval('a', 0, 100), interval('b', 0, 100)],
            'events': [event('a', 40, 'failure'), event('b', 70, 'retire')],
            'repairs': [repair('a', 50, 'minor')],
        }
        episodes = build_episodes(tables, 200)
        rows = risk_table(episodes)
        failure_row = next(row for row in rows if row['age'] == 40)
        retire_row = next(row for row in rows if row['age'] == 70)
        self.assertEqual(failure_row['failures'], 1)
        self.assertEqual(failure_row['at_risk'], 2)
        self.assertEqual(retire_row['retires'], 1)

    def test_homogeneity_flags_concentrated_failures(self):
        machines_list = ([machine(f'a{i}', '甲') for i in range(6)] +
                         [machine(f'b{i}', '乙') for i in range(6)])
        tables = {
            'machines': machines_list,
            'intervals': [interval(m['machine'], 0, 100) for m in machines_list],
            'events': [event(f'a{i}', 50, 'failure') for i in range(6)],
            'repairs': [],
        }
        evidence = homogeneity_evidence(build_episodes(tables, 200))
        # 六次故障全部集中在甲站：共同率（各站暴露相等）下 P=0.5^6
        self.assertLess(evidence['conditional_binomial_p_value'], 0.05)
        self.assertEqual(evidence['failures_by_site'].get('乙', 0), 0)
        self.assertEqual(evidence['concentration_site'], '甲')

    def test_beyond_observed_status_uses_constant(self):
        from fleet_reliability.reliability import conditional_failure_probability
        rate_info = {'rate': 0.001, 'rate_lower': 0.0005, 'rate_upper': 0.002}
        result = conditional_failure_probability(150, 50, rate_info, 90)
        self.assertEqual(result['status'], BEYOND_OBSERVED)
        self.assertIsNone(result['probability'])
        self.assertIsNotNone(result['extrapolation']['probability'])

    def test_post_repair_zero_failures_only_returns_bound(self):
        # 维修后片段零故障：rate_estimate 给零事件上界，不产生点估计假象
        from fleet_reliability.statistics import poisson_rate_interval
        lower, upper = poisson_rate_interval(0, 5000)
        self.assertEqual(lower, 0.0)
        self.assertGreater(upper, 0.0)
        self.assertEqual(INSUFFICIENT, 'insufficient_post_repair_evidence')


if __name__ == '__main__':
    unittest.main()
