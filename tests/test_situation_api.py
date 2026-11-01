import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sim import situation_api as api


class SituationAPITests(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(api.router);self.client=TestClient(app)
        self.addCleanup(self.client.close)
        self.area='area_'+'a'*20
        self.descriptor={'assets':{},'clip_bounds_local_m':[0,0,100,100],'requested_bbox':[77.2,28.6,77.21,28.61],'metrics_scope':'full_cached_aoi','metrics_bbox':[77.2,28.58,77.32,28.72]}

    def test_report_enters_response_without_claiming_verification(self):
        with patch.object(api,'selected_area',return_value=self.descriptor),patch.object(api,'current_observations',return_value=[]):
            response=self.client.post('/situation/assess',json={'area_id':self.area,'report':{'kind':'flood','description':'User-reported incident','confirmed_current':True}})
        self.assertEqual(response.status_code,200)
        data=response.json();self.assertEqual(data['situation']['mode'],'response')
        self.assertEqual(data['situation']['status'],'reported_unverified')
        self.assertEqual(data['stations'],[])
        self.assertFalse(data['checklist']['automatic_dispatch'])

    def test_client_cannot_supply_verified_observations(self):
        result=self.client.post('/situation/assess',json={'area_id':self.area,'trusted_observations':[{'verified':True}]})
        self.assertEqual(result.status_code,422)

    def test_no_report_stays_simulation(self):
        with patch.object(api,'selected_area',return_value=self.descriptor),patch.object(api,'current_observations',return_value=[]):
            result=self.client.post('/situation/assess',json={'area_id':self.area}).json()
        self.assertEqual(result['situation']['mode'],'simulation')

    def test_other_model_cannot_borrow_delhi_run_metrics(self):
        descriptor={**self.descriptor,'metrics_scope':'unavailable_for_model'}
        with patch.object(api,'selected_area',return_value=descriptor),patch.object(api,'current_observations',return_value=[]),patch.object(api.gemini_api,'briefing_context') as briefing:
            result=self.client.post('/situation/assess',json={'area_id':self.area,'run_id':'product_'+'a'*16})
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.json()['data_scope']['run_omitted'])
        self.assertFalse(any(item['id']=='run' for item in result.json()['checklist']['evidence']))
        briefing.assert_not_called()


if __name__=='__main__':unittest.main()
