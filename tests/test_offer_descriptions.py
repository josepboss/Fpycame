import unittest
from admin.services.offer_descriptions import imported_description, LIMIT
from locales.localizer import Localizer


class DescriptionPolicyTests(unittest.TestCase):
    def test_complete_policy_preserved_within_limit_and_idempotent(self):
        for language in ('en','ru'):
            policy=Localizer().translate('oi_replacement_policy',language=language)
            for text in ('Short details','x'*2000,'Line\r\n'*200):
                result=imported_description(text,language)
                self.assertLessEqual(len(result),LIMIT)
                self.assertTrue(result.endswith(policy))
                self.assertEqual(result.count(policy),1)
                self.assertEqual(imported_description(result,language),result)
                self.assertNotIn('\r',result)
