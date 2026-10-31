import configparser
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open
from first_setup import default_config
from Utils.config_loader import create_config_obj, load_main_config


class ConfigCompatibilityTests(unittest.TestCase):
    def test_colon_and_equals_delimiters(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.cfg'
            path.write_text('[FunPay]\ngolden_key = example\nautoRaise: 1\n')
            cfg=create_config_obj(str(path))
            self.assertEqual(cfg['FunPay']['golden_key'],'example')
            self.assertEqual(cfg['FunPay']['autoRaise'],'1')
    def test_admin_lowercase_main_keys_are_normalized(self):
        data=copy.deepcopy(default_config)
        data['FunPay']['golden_key']='test-key'
        data['FunPay']['keepSentMessagesUnread']='0'
        cfg=configparser.ConfigParser(interpolation=None)
        cfg.read_dict(data)
        cfg.optionxform=str
        with patch('Utils.config_loader.create_config_obj',return_value=cfg),patch('builtins.open',mock_open()):
            result=load_main_config('unused.cfg')
        self.assertEqual(result['FunPay']['autoRaise'],'0')
        self.assertEqual(result['Telegram']['secretKeyHash'],data['Telegram']['secretKeyHash'])
