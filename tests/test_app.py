import tempfile
import unittest
from unittest.mock import patch
import os
from sim import launch
from pathlib import Path
from fastapi.testclient import TestClient
from sim.app import create_app


class OneServerTests(unittest.TestCase):
    def test_spa_and_api_share_origin_but_missing_data_is_not_html(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);web=base/'web';data=base/'data'
            (web/'assets').mkdir(parents=True);data.mkdir()
            (web/'index.html').write_text('<html>otec</html>')
            (data/'terrain.json').write_text('{"real":true}')
            with TestClient(create_app(web,data)) as client:
                self.assertEqual(client.get('/').status_code,200)
                self.assertEqual(client.get('/api/health').json()['status'],'ok')
                self.assertTrue(client.get('/data/terrain.json').json()['real'])
                self.assertEqual(client.get('/data/missing.json').status_code,404)
                self.assertEqual(client.get('/missing.bin').status_code,404)
                self.assertEqual(client.get('/private/.hidden').status_code,404)

    def test_single_command_forces_background_compute_off(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'web/dist').mkdir(parents=True);(root/'web/dist/index.html').write_text('otec')
            with patch.object(launch,'ROOT',root),patch.dict(os.environ,{'OTEC_ENABLE_LOCAL_COMPUTE':'1','OTEC_WATCH_EXPORTS':'1'}),patch('socket.socket'),patch('uvicorn.run') as serve:
                launch.main(['--no-build'])
                self.assertEqual(os.environ['OTEC_ENABLE_LOCAL_COMPUTE'],'0')
                self.assertEqual(os.environ['OTEC_WATCH_EXPORTS'],'0')
                serve.assert_called_once()


if __name__=='__main__':unittest.main()
