"""Isolated provider/cache fixtures; never access real credentials or network."""
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import requests
from sim import imagery, serve


class ImageryTests(unittest.TestCase):
    def setUp(self):
        self.now=100.0; self.keys={"mapbox":"unit-mapbox","esri":""}
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        states={p:{"credits":p+" attribution","credits_time":self.now,"until":0,"reason":None,"tile":"untested","failures":0} for p in imagery.PROVIDERS}
        patches=[patch.object(imagery,"provider_token",side_effect=lambda p:self.keys[p]),patch.object(imagery.requests,"get"),
                 patch.object(imagery.time,"monotonic",side_effect=lambda:self.now),patch.object(imagery.time,"time",side_effect=lambda:self.now),
                 patch.object(imagery,"CACHE_DIR",Path(directory.name)),patch.object(imagery,"_states",states),
                 patch.object(imagery,"_tiles",imagery.OrderedDict()),patch.object(imagery,"_key_locks",{})]
        values=[item.start() for item in patches]
        self.key,self.get=values[:2]
        for item in patches:self.addCleanup(item.stop)
        self.coords=(16,*imagery.tile_xy(77.25,28.65,16))

    def response(self,code=200,data=b'isolated-jpeg-fixture',headers=None):
        return Mock(status_code=code,content=data,headers={"Content-Type":"image/jpeg","Cache-Control":"public, max-age=120",**(headers or {})})

    def test_missing_credentials_and_outside_aoi_do_not_request(self):
        self.keys["mapbox"]=""
        self.assertFalse(imagery.status()["available"])
        self.assertEqual(imagery.tile(*self.coords).status_code,204)
        self.key.reset_mock()
        self.assertEqual(imagery.tile(16,0,0).status_code,204)
        self.key.assert_not_called();self.get.assert_not_called()

    def test_metadata_alone_does_not_claim_tile_success(self):
        status=imagery.status()
        self.assertEqual(status["provider"],"mapbox")
        self.assertEqual(status["tile_availability"],"untested")
        self.assertEqual(status["maximum_concurrent_requests"],6)

    def test_success_memory_and_disk_cache_without_credentials_on_disk(self):
        self.get.return_value=self.response(headers={"ETag":"version-one"})
        first=imagery.tile(*self.coords);second=imagery.tile(*self.coords)
        self.assertEqual(first.status_code,200);self.assertEqual(first.body,second.body)
        imagery._tiles.clear()
        disk=imagery.tile(*self.coords)
        self.assertEqual(disk.headers["X-Imagery-Cache"],"hit")
        self.get.assert_called_once()
        self.assertNotIn(b'unit-mapbox',(imagery.CACHE_DIR/'tiles.sqlite3').read_bytes())

    def test_expired_cache_revalidates_with_etag_and_304(self):
        self.get.return_value=self.response(headers={"ETag":"version-one","Cache-Control":"max-age=1"})
        original=imagery.tile(*self.coords);self.now+=2
        self.get.return_value=self.response(304,data=b'',headers={"Cache-Control":"max-age=300"})
        refreshed=imagery.tile(*self.coords)
        self.assertEqual(refreshed.body,original.body)
        self.assertEqual(self.get.call_args.kwargs["headers"]["If-None-Match"],"version-one")

    def test_no_store_is_not_persisted(self):
        self.get.return_value=self.response(headers={"Cache-Control":"no-store"})
        imagery.tile(*self.coords);imagery.tile(*self.coords)
        self.assertEqual(self.get.call_count,2)
        self.assertFalse(imagery._tiles)

    def test_mapbox_rate_limit_falls_back_to_esri_with_correct_xyz_order(self):
        self.keys["esri"]="unit-esri"
        self.get.side_effect=[self.response(429,headers={"Retry-After":"90"}),self.response(data=b'esri-fixture')]
        result=imagery.tile(*self.coords)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers["X-Imagery-Provider"],"esri")
        z,x,y=self.coords
        self.assertTrue(self.get.call_args.args[0].endswith(f'/tile/{z}/{y}/{x}'))
        self.assertEqual(imagery.status()["provider"],"esri")
        self.assertEqual(imagery.availability("mapbox")["retry_after_s"],90)

    def test_backoff_stops_network_globally_and_increases_exponentially(self):
        self.get.return_value=self.response(429,headers={"Retry-After":"30"})
        for expected in (30,60,120,240,300):
            imagery.tile(*self.coords)
            self.assertEqual(imagery.availability("mapbox")["retry_after_s"],expected)
            calls=self.get.call_count;z,x,y=self.coords;imagery.tile(z,x+1,y)
            self.assertEqual(self.get.call_count,calls)
            self.now+=expected+1

    def test_retry_after_seconds_and_dates_are_bounded(self):
        for value,expected in (("0",30),("900",300),("nan",60),("broken",60),(None,60)):
            self.assertEqual(imagery.retry_delay(value),expected)
        self.assertEqual(imagery.retry_delay(formatdate(self.now+120,usegmt=True)),120)

    def test_network_failure_falls_back_to_matte_without_secret_error(self):
        self.get.side_effect=requests.ConnectionError('https://upstream.invalid/?token=unit-mapbox')
        result=imagery.tile(*self.coords);status=imagery.status()
        self.assertEqual(result.status_code,204);self.assertEqual(result.body,b'')
        self.assertFalse(status["available"])
        self.assertNotIn('unit-mapbox',str(status));self.assertNotIn('upstream.invalid',str(status))
        imagery.tile(*self.coords);self.get.assert_called_once()

    def test_cached_tile_remains_available_during_cooldown(self):
        self.get.return_value=self.response();original=imagery.tile(*self.coords)
        imagery.backoff("mapbox","rate_limited","30")
        self.assertEqual(imagery.tile(*self.coords).body,original.body)
        self.get.assert_called_once()

    def test_at_most_six_upstream_tiles_execute_concurrently(self):
        lock=threading.Lock();active=0;peak=0
        def request(*args,**kwargs):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            time.sleep(.03)
            with lock:active-=1
            return self.response()
        self.get.side_effect=request
        z,x,y=self.coords
        with ThreadPoolExecutor(max_workers=12) as executor:
            responses=list(executor.map(lambda i:imagery.tile(z,x+(i%4),y+(i//4)),range(12)))
        self.assertTrue(all(response.status_code==200 for response in responses))
        self.assertLessEqual(peak,6);self.assertGreater(peak,1)


class LauncherTests(unittest.TestCase):
    def test_default_launcher_reads_no_file(self):
        with patch.object(serve,"load_imagery_tokens") as loader,patch.object(serve.subprocess,"run",return_value=Mock(returncode=0)) as run:
            self.assertEqual(serve.main(["--port","8123"]),0)
        loader.assert_not_called();self.assertIsNone(run.call_args.kwargs["env"])

    def test_explicit_launcher_passes_only_imagery_keys_in_memory(self):
        values={"VITE_MAPBOX_TOKEN":"unit-mapbox","VITE_ARCGIS_KEY":"unit-esri"}
        with patch.object(serve,"load_imagery_tokens",return_value=values) as loader,patch.object(serve.os.environ,"copy",return_value={}),patch.object(serve.subprocess,"run",return_value=Mock(returncode=0)) as run:
            serve.main(["--imagery-token-file","/explicit/user-approved/file"])
        loader.assert_called_once_with('/explicit/user-approved/file')
        self.assertEqual(run.call_args.kwargs["env"],values)
        self.assertNotIn('unit-mapbox',str(run.call_args.args));self.assertNotIn('unit-esri',str(run.call_args.args))

    def test_parser_ignores_unrelated_values_without_shell_evaluation(self):
        values=serve.imagery_values(['OTHER_SECRET=ignore','MAPBOX_TOKEN=fallback','VITE_MAPBOX_TOKEN=primary','VITE_ARCGIS_KEY="$(never-execute)"'])
        self.assertEqual(values,{"VITE_MAPBOX_TOKEN":"primary","VITE_ARCGIS_KEY":"$(never-execute)"})


if __name__=='__main__':unittest.main()
