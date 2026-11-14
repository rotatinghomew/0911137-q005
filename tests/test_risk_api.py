import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from fleet_reliability.application import ROOT, server
from fleet_reliability.application import Application
from pathlib import Path
import tempfile


class RiskApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.application = Application(ROOT / 'fixtures/data.json',
                                       Path(self.directory.name) / 'state.sqlite3', ROOT / 'web')
        self.httpd = server(self.application)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown()
        self.worker.join()
        self.httpd.server_close()
        self.directory.cleanup()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return json.load(response)

    def test_view_contains_risk_with_explicit_statuses(self):
        view = self.get('/api/view')
        risk = view['result']['risk']
        self.assertEqual(risk['horizon_hours'], 720)
        # 跨站点借用检验给出明确结论
        self.assertFalse(risk['homogeneity']['pooling_supported'])
        self.assertLess(risk['homogeneity']['conditional_binomial_p_value'], 0.001)
        # 站点2/3 零故障：率点估计为零但保留上界
        for site in ('站点2', '站点3'):
            self.assertEqual(risk['site_rates'][site]['failures'], 0)
            self.assertGreater(risk['site_rates'][site]['rate_upper'], 0)
        # 每台在用设备都有明确状态，不允许凭空精确风险
        statuses = {row['forecast']['status'] for row in risk['machines']}
        self.assertTrue(statuses <= {'active', 'retired', 'beyond_observed_age',
                                     'insufficient_post_repair_evidence'})
        for row in risk['machines']:
            if row['forecast']['status'] == 'beyond_observed_age':
                self.assertIsNone(row['forecast']['probability'])
                self.assertIsNotNone(row['forecast'].get('extrapolation'))

    def test_spare_part_range_has_strict_and_model_layers(self):
        risk = self.get('/api/view')['result']['risk']
        scenarios = risk['spare_part_scenarios']
        self.assertEqual(scenarios['recommendation'], 'site_specific')
        self.assertIn('strict_expected_failures', scenarios['site_specific'])
        self.assertIn('model_expected_failures', scenarios['site_specific'])
        # 借用情景只作为敏感性对照
        self.assertIn('敏感性', scenarios['pooled_sensitivity']['label'])
        forecast = risk['fleet_forecast']
        self.assertIsNotNone(forecast['model_poisson_prediction_interval'])
        self.assertIsNotNone(forecast['conservative_upper_need'])

    def test_grouped_view_keeps_fleet_borrowing_evidence(self):
        # 站点2 自身零故障，但等率检验与全网故障年龄仍须来自全部站点
        risk = self.get('/api/view?group=%E7%AB%99%E7%82%B92')['result']['risk']
        self.assertIsNotNone(risk['homogeneity']['max_failure_age'])
        self.assertFalse(risk['homogeneity']['pooling_supported'])
        # 借用全网率的敏感性情景对零故障站点给出非零期望，主口径仍为站点自身
        pooled = risk['spare_part_scenarios']['pooled_sensitivity']
        self.assertGreater(pooled['model_expected_failures'], 0)
        self.assertEqual(risk['spare_part_scenarios']['recommendation'], 'site_specific')

    def test_post_repair_evidence_is_bound_only(self):
        risk = self.get('/api/view')['result']['risk']
        post = risk['post_repair']
        self.assertEqual(post['failures'], 0)
        self.assertGreater(post['exposure'], 0)
        self.assertGreater(post['rate_upper_95'], 0)
        self.assertEqual(post['status'], 'insufficient_post_repair_evidence')

    def test_historical_cutoff_hides_future_information(self):
        # 故障在业务时标 1000、获知 1010；900 时点零故障可见
        early = self.get('/api/view?cutoff=900')['result']['risk']
        self.assertEqual(early['pooled_first_life_rate']['failures'], 0)
        self.assertIsNone(early['homogeneity']['max_failure_age'])
        # 维修在 1100：1050 时点大修尚不可见，片段数等于设备数
        mid = self.get('/api/view?cutoff=1050')['result']['risk']
        self.assertIsNone(mid['post_repair'])
        self.assertEqual(len(mid['episodes']), 48)

    def test_machine_detail_endpoint(self):
        detail = self.get('/api/machine?machine=m003')
        self.assertEqual(detail['machine']['machine'], 'm003')
        self.assertIn('forecast', detail['machine'])
        # 表单会同时携带 group，接口必须接受而不是 400
        scoped = self.get('/api/machine?machine=m003&group=%E7%AB%99%E7%82%B91')
        self.assertEqual(scoped['machine']['machine'], 'm003')
        with self.assertRaises(HTTPError) as caught:
            self.get('/api/machine?machine=missing')
        self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            self.get('/api/machine?machine=m003&bogus=1')
        self.assertEqual(caught.exception.code, 400)

    def test_horizon_validation(self):
        for bad in ['0', '-5', '1.5']:
            with self.assertRaises(HTTPError) as caught:
                self.get(f'/api/view?horizon_hours={bad}')
            self.assertEqual(caught.exception.code, 400)
        good = self.get('/api/view?horizon_hours=360')
        self.assertEqual(good['options']['horizon_hours'], 360)

    def test_risk_result_is_frozen_when_saved(self):
        view = self.get('/api/view')
        from urllib.request import Request
        payload = json.dumps({'title': '风险版本', 'options': view['options']}).encode()
        request = Request(self.base + '/api/analyses', data=payload,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            saved = json.load(response)
        restored = self.get('/api/analyses/' + saved['id'])
        self.assertEqual(restored['result']['risk'], view['result']['risk'])


if __name__ == '__main__':
    unittest.main()
