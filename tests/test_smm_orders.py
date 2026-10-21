import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from plugins.providers import smm_orders as smm


class SMMOrdersTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        p=patch.object(smm,'DB_PATH',Path(self.folder.name)/'smm.db');p.start();self.addCleanup(p.stop)
        self.crd=SimpleNamespace(account=SimpleNamespace(id=7),send_message=Mock(return_value=[Mock()]))
        self.order=SimpleNamespace(id='SMM-TEST',chat_id=123,buyer_username='demo')

    def response(self, payload):
        response=Mock(status_code=200);response.json.return_value=payload;return response

    def place(self):
        smm.place(self.crd,self.order,'55',100,'https://example.test/profile',Mock(return_value=self.response({'order':789})))
        self.crd.send_message.reset_mock()
        self.due()

    def due(self):
        with smm.database() as db:db.execute('UPDATE orders SET next_check=0')

    def row(self):
        with smm.database() as db:return dict(db.execute('SELECT * FROM orders').fetchone())

    def test_started_only_after_provider_accepts_and_id_is_saved(self):
        def add(params):
            self.crd.send_message.assert_not_called()
            self.assertEqual(params['action'],'add')
            return self.response({'order':789})
        smm.place(self.crd,self.order,'55',100,'demo',add)
        self.assertEqual(self.row()['supplier_order'],'789')
        self.assertEqual(self.row()['state'],'pending')
        self.assertIn('several hours',self.crd.send_message.call_args.args[1])

    def test_pending_for_multiple_hours_does_not_send_false_failure(self):
        self.place()
        for status in ['Pending','In progress','Processing','In Progress']:
            with patch.object(smm.time,'time',return_value=2000000000):
                self.due();smm.check_pending(self.crd,Mock(return_value=self.response({'status':status})))
            self.assertEqual(self.row()['state'],'pending')
        self.crd.send_message.assert_not_called()

    def test_completed_after_hours_notifies_once_and_never_adds_again(self):
        self.place()
        request=Mock(return_value=self.response({'status':'Completed'}))
        with patch.object(smm.time,'time',return_value=2000000000):
            smm.check_pending(self.crd,request)
        self.assertEqual(self.row()['state'],'completed')
        self.assertIn('Order completed',self.crd.send_message.call_args.args[1])
        self.due();smm.check_pending(self.crd,request)
        self.crd.send_message.assert_called_once()
        self.assertEqual(request.call_args.args[0],{'action':'status','order':'789'})

    def test_temporary_network_and_bad_replies_leave_service_pending(self):
        self.place()
        broken=self.response({});broken.json.side_effect=ValueError('synthetic')
        for response in [None,Mock(status_code=503),broken,self.response({'error':'temporary'}),self.response({'status':None})]:
            self.due();smm.check_pending(self.crd,Mock(return_value=response))
            self.assertEqual(self.row()['state'],'pending')
        self.crd.send_message.assert_not_called()

    def test_ambiguous_placement_is_neutral_and_cannot_buy_twice(self):
        request=Mock(return_value=None)
        for _ in range(2):smm.place(self.crd,self.order,'55',100,'demo',request)
        request.assert_called_once()
        self.assertEqual(self.row()['state'],'uncertain')
        self.assertIn('verifying confirmation',self.crd.send_message.call_args.args[1])
        self.assertNotIn('Error',self.crd.send_message.call_args.args[1])

    def test_explicit_rejection_and_cancellation_are_actual_failures(self):
        smm.place(self.crd,self.order,'55',100,'demo',Mock(return_value=self.response({'error':'Not enough funds'})))
        self.assertEqual(self.row()['state'],'failed')
        self.assertIn('could not start',self.crd.send_message.call_args.args[1])
        with smm.database() as db:db.execute('DELETE FROM orders')
        self.place()
        smm.check_pending(self.crd,Mock(return_value=self.response({'status':'Canceled'})))
        self.assertEqual(self.row()['state'],'failed')
        self.assertIn('cancelled or failed',self.crd.send_message.call_args.args[1])

    def test_partial_validates_remains_and_reports_correct_quantity(self):
        self.place()
        for remains in ['invalid',None,-1,101,True]:
            self.due();smm.check_pending(self.crd,Mock(return_value=self.response({'status':'Partial','remains':remains})))
            self.assertEqual(self.row()['state'],'pending')
        self.due();smm.check_pending(self.crd,Mock(return_value=self.response({'status':'Partial','remains':'20'})))
        self.assertEqual(self.row()['state'],'partial')
        self.assertIn('80/100',self.crd.send_message.call_args.args[1])
