import unittest
from admin.services.offer_titles import imported_title, has_delivery_prefix


class OfferTitleTests(unittest.TestCase):
    def test_english_prefix_and_total_limit(self):
        result=imported_title('Twitter '+('X'*100))
        self.assertTrue(result.startswith('instant delivery '))
        self.assertEqual(len(result),100)
        self.assertTrue(has_delivery_prefix(result))

    def test_repeated_prefix_is_not_duplicated(self):
        self.assertEqual(imported_title('Instant Delivery: instant delivery - Twitter'), 'instant delivery Twitter')
        self.assertEqual(imported_title(imported_title('Twitter')),'instant delivery Twitter')

    def test_russian_translation_has_its_own_prefix(self):
        self.assertEqual(imported_title('Аккаунты Twitter','ru'),'мгновенная доставка Аккаунты Twitter')
        self.assertEqual(imported_title('instant delivery Аккаунты','ru'),'мгновенная доставка Аккаунты')
        self.assertEqual(imported_title(''), '')
