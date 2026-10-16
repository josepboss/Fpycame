import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from admin.main import app
from admin.auth import get_current_user


class AdminPageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        for name,value in {
            'admin.routes.settings.MAIN_CFG':str(folder/'main.cfg'),
            'admin.routes.settings.API_KEYS_CFG':str(folder/'keys.cfg'),
            'admin.routes.providers.API_KEYS_CFG':str(folder/'keys.cfg'),
            'admin.routes.providers.PROVIDERS_CFG':str(folder/'providers.json'),
            'admin.routes.providers.HEALTH_CACHE':str(folder/'health.json'),
            'admin.routes.logs.LOG_FILE':str(folder/'log.log'),
        }.items():
            p=patch(name,value);p.start();self.addCleanup(p.stop)
        p=patch('admin.routes.status.get_pm2_status',return_value=('online',1700000000000))
        p.start();self.addCleanup(p.stop)
        app.dependency_overrides[get_current_user]=lambda:'test-admin'
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app,raise_server_exceptions=False)
        self.folder=folder

    def test_all_navigation_pages_render(self):
        (self.folder/'main.cfg').write_text('[FunPay]\ngolden_key = configured\n')
        for path, heading in [('/providers/','Providers'),('/settings/','Settings'),('/logs/','Live Logs'),('/status/','Cardinal Status')]:
            with self.subTest(path=path):
                response=self.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertIn(heading,response.text)

    def test_settings_missing_config_does_not_crash(self):
        self.assertEqual(self.client.get('/settings/').status_code,200)

    def test_provider_timestamps_have_registered_filter(self):
        (self.folder/'health.json').write_text(json.dumps({'hstore':{'last_success_time':1700000000,'last_error_time':1700000060}}))
        response=self.client.get('/providers/')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('N/A',response.text)

    def test_status_handles_missing_pm2(self):
        with patch('admin.routes.status.get_pm2_status',return_value=('unknown',0)):
            response=self.client.get('/status/')
        self.assertEqual(response.status_code,200)
        self.assertIn('UNKNOWN',response.text)


if __name__=='__main__':unittest.main()
