import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace as Obj
from unittest.mock import patch
from decimal import Decimal
from fastapi.testclient import TestClient
from admin.main import app
from admin.auth import get_current_user
from admin.services import analytics as service


def order(id, status='CLOSED', currency='USD', price='1.20', amount=2):
    return Obj(id=id,description='Example, 2 pcs.',subcategory_name='Accounts',currency=Obj(name=currency),price=price,amount=amount,status=Obj(name=status),date=datetime.now())


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patch=patch.object(service,'DB_PATH',Path(self.tmp.name)/'sales.db')
        self.patch.start()
    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()
    def sync(self, account):
        if not hasattr(account, "get_user"):
            account.get_user=lambda *args,**kwargs:Obj(get_lots=lambda:[])
        service._LOCK.acquire()
        with patch('admin.services.offer_import.make_account',return_value=account),patch.object(service.time,'sleep'):
            service.sync_sales()
    def test_paginated_snapshot_deduplicates_and_separates_status_and_currency(self):
        account=Obj(id=1,get_sales=__import__('unittest.mock',fromlist=['Mock']).Mock(side_effect=[('next',[order('A'),order('P','PAID')],'en',{}),(None,[order('A'),order('R','REFUNDED'),order('B',currency='RUB',price='20')],'en',{})]))
        self.sync(account)
        data=service.report(0)
        totals={v['currency']:v for v in data['totals']}
        self.assertEqual(totals['USD']['sales'],1)
        self.assertEqual(totals['USD']['revenue'],Decimal('1.20'))
        self.assertEqual(totals['USD']['pending'],1)
        self.assertEqual(totals['USD']['refunds'],1)
        self.assertEqual(data['groups']['USD'][0]['title'],'Example')
        account.get_sales.side_effect=[(None,[order('A','REFUNDED')],'en',{})]
        self.sync(account)
        self.assertEqual(service.report(0)['totals'][0]['sales'],0)
    def test_failure_preserves_snapshot(self):
        from unittest.mock import Mock
        account=Obj(id=1,get_sales=Mock(return_value=(None,[order('A')],'en',{})))
        self.sync(account)
        account.get_sales.side_effect=TimeoutError()
        self.sync(account)
        data=service.report(0)
        self.assertEqual(data['meta']['state'],'failed')
        self.assertEqual(data['totals'][0]['sales'],1)
    def test_page_and_csrf(self):
        app.dependency_overrides[get_current_user]=lambda:'test'
        try:
            with patch('admin.routes.analytics.get_csrf_token',return_value='test'),patch('admin.routes.analytics.validate_csrf_token',return_value=False):
                client=TestClient(app)
                self.assertEqual(client.get('/analytics/').status_code,200)
                self.assertEqual(client.post('/analytics/refresh',data={'csrf_token':'wrong'}).status_code,403)
        finally:
            app.dependency_overrides.clear()

    def test_current_listings_include_zero_sales_and_edit_links(self):
        from unittest.mock import Mock
        sub=Obj(id=1259,type=Obj(name='COMMON'),category=Obj(name='Twitter'),name='Accounts')
        lots=[Obj(id=123,subcategory=sub,description='Example'),Obj(id=124,subcategory=sub,description='Never sold')]
        sale=order('A')
        sale.subcategory_name='Twitter, Accounts'
        account=Obj(id=1,get_user=Mock(return_value=Obj(get_lots=lambda:lots)),get_sales=Mock(return_value=(None,[sale],'en',{})))
        self.sync(account)
        data=service.report(0)
        self.assertEqual([(v['title'],v['sales']) for v in data['listings']],[('Never sold',0),('Example',1)])
        self.assertIn('node=1259&offer=124',data['listings'][0]['edit_url'])
        # Removed listings disappear after refresh; past sales remain in rankings.
        account.get_user.return_value=Obj(get_lots=lambda:[])
        self.sync(account)
        self.assertEqual(service.report(0)['listings'],[])
        self.assertEqual(service.report(0)['totals'][0]['sales'],1)
