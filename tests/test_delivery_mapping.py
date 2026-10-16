import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from admin.services import delivery_mapping as delivery


class DeliveryMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        for name,value in [('API_CFG',folder/'api.cfg'),('AUTO_CFG',folder/'auto.cfg'),('PRODUCTS',folder/'products')]:
            p=patch.object(delivery,name,value)
            p.start()
            self.addCleanup(p.stop)

    def test_existing_inventory_and_custom_response_are_preserved(self):
        title='Twitter [Mail+2fa] 100%'
        delivery.save_delivery_mapping(title,'hstore','1358',False)
        api,auto=delivery.read_configs()
        auto[title]['response']='Custom delivery for $username:\n$product'
        auto[title]['disableMultiDelivery']='1'
        delivery.atomic_write(auto,delivery.AUTO_CFG)
        stock=delivery.PRODUCTS/auto[title]['productsFileName']
        stock.write_text('existing-credentials\n')
        delivery.save_delivery_mapping(title,'hstore','1358',True,imported=True)
        api,auto=delivery.read_configs()
        self.assertEqual(stock.read_text(),'existing-credentials\n')
        self.assertEqual(auto[title]['response'],'Custom delivery for $username:\n$product')
        self.assertEqual(auto[title]['disableMultiDelivery'],'1')
        self.assertEqual(api[title]['enabled'],'1')

    def test_sanitized_names_do_not_share_inventory_file(self):
        for title in ['Twitter + email','Twitter / email']:
            delivery.save_delivery_mapping(title,'hstore','1358',True,imported=True)
        _,auto=delivery.read_configs()
        self.assertNotEqual(auto['Twitter + email']['productsFileName'],auto['Twitter / email']['productsFileName'])

    def test_conflict_does_not_overwrite_existing_mapping(self):
        delivery.save_delivery_mapping('Twitter','smmcost','777',True)
        before_api=delivery.API_CFG.read_bytes()
        before_auto=delivery.AUTO_CFG.read_bytes()
        with self.assertRaises(delivery.MappingConflict):
            delivery.save_delivery_mapping('Twitter','hstore','1358',True,imported=True)
        self.assertEqual(delivery.API_CFG.read_bytes(),before_api)
        self.assertEqual(delivery.AUTO_CFG.read_bytes(),before_auto)

    def test_unrelated_mapping_survives_new_import(self):
        delivery.save_delivery_mapping('Existing','smmcost','777',True)
        delivery.save_delivery_mapping('Imported','hstore','1358',True,imported=True)
        api,auto=delivery.read_configs()
        self.assertEqual(api['Existing']['service_id'],'777')
        self.assertEqual(api['Existing']['provider'],'smmcost')
        self.assertIn('Existing',auto)


if __name__=='__main__':unittest.main()
