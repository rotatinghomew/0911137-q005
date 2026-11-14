import math
import unittest

from fleet_reliability.statistics import (
    chi_square_quantile,
    normal_quantile,
    poisson_cdf,
    poisson_quantile,
    poisson_rate_interval,
    regularized_gamma,
)


class StatisticsTests(unittest.TestCase):
    def test_normal_quantile_known_values(self):
        self.assertAlmostEqual(normal_quantile(0.5), 0.0, places=9)
        self.assertAlmostEqual(normal_quantile(0.975), 1.959963984, places=7)
        self.assertAlmostEqual(normal_quantile(0.025), -1.959963984, places=7)

    def test_chi_square_quantile_known_values(self):
        self.assertAlmostEqual(chi_square_quantile(0.95, 2), 5.9914645, places=5)
        self.assertAlmostEqual(chi_square_quantile(0.025, 32), 18.29076, places=4)
        self.assertAlmostEqual(chi_square_quantile(0.975, 32), 49.48044, places=4)

    def test_regularized_gamma_limits(self):
        self.assertEqual(regularized_gamma(1, 0), 0.0)
        self.assertAlmostEqual(regularized_gamma(1, 20), 1.0, places=8)

    def test_poisson_interval_matches_tables(self):
        lower, upper = poisson_rate_interval(16, 1000)
        # 已知精确 Poisson 区间：计数 16 的 95% 区间约为 (9.16, 25.98)
        self.assertAlmostEqual(lower * 1000, 9.155, delta=0.02)
        self.assertAlmostEqual(upper * 1000, 25.983, delta=0.02)

    def test_zero_events_exact_bound(self):
        lower, upper = poisson_rate_interval(0, 3000)
        self.assertEqual(lower, 0.0)
        # 精确上界 = chi2(0.975, 2)/2 / n；3/n 是其保守近似
        self.assertAlmostEqual(upper * 3000, chi_square_quantile(0.975, 2) / 2, places=9)
        self.assertLessEqual(upper, 3.0 / 3000 * 1.25)

    def test_poisson_cdf_and_quantile(self):
        self.assertAlmostEqual(poisson_cdf(0, 5), math.exp(-5), places=12)
        self.assertEqual(poisson_quantile(0.5, 5.0), 5)
        self.assertEqual(poisson_quantile(0.025, 5.0), 1)
        self.assertEqual(poisson_quantile(0.975, 5.0), 10)

    def test_invalid_inputs_rejected(self):
        for bad in [0.0, 1.0, -0.1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                normal_quantile(bad)
        with self.assertRaises(ValueError):
            poisson_rate_interval(-1, 10)
        with self.assertRaises(ValueError):
            poisson_rate_interval(1, 0)
