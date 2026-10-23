import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from admin.services import offer_translation as tr
from admin.services.offer_import import ImportFailure


class TranslationTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        p=patch.object(tr,'DB_PATH',Path(folder.name)/'translation.db');p.start();self.addCleanup(p.stop)

    def response(self,text,status=200):
        r=Mock(status_code=200)
        r.json.return_value={'responseStatus':status,'responseData':{'translatedText':text}}
        return r

    def test_russian_source_is_preserved_and_english_is_translated(self):
        with patch.object(tr,'translate_text',side_effect=['Mail accounts','Email and password included']) as translate:
            result=tr.bilingual_listing('Почтовые аккаунты','Почта и пароль в комплекте')
        self.assertEqual(result['title_ru'],'Почтовые аккаунты')
        self.assertEqual(result['title_en'],'Mail accounts')
        self.assertEqual(result['description_ru'],'Почта и пароль в комплекте')
        self.assertEqual(result['description_en'],'Email and password included')
        self.assertEqual(translate.call_args_list[0].args[1:],('ru','en'))

    def test_english_source_preserved_and_mixed_fields_detected_separately(self):
        with patch.object(tr,'translate_text',side_effect=['Почтовые аккаунты','Email and password included']):
            result=tr.bilingual_listing('Mail accounts','Почта и пароль в комплекте')
        self.assertEqual(result['title_en'],'Mail accounts')
        self.assertEqual(result['title_ru'],'Почтовые аккаунты')
        self.assertEqual(result['description_ru'],'Почта и пароль в комплекте')
        self.assertEqual(result['description_en'],'Email and password included')

    def test_both_languages_obey_field_limits(self):
        with patch.object(tr,'translate_text',return_value='Я'*1500):
            result=tr.bilingual_listing('A'*150,'B'*1000)
        for key,value in result.items():
            self.assertEqual(len(value),100 if key.startswith('title') else 817)

    def test_utf8_chunking_line_breaks_and_cache(self):
        calls=[]
        def request(url,**kwargs):
            text=kwargs['params']['q'];calls.append(text)
            self.assertLessEqual(len(text.encode()),500)
            return self.response('English translated segment')
        source=('Аккаунты '*100)+'\n\n'+('Почта '*30)
        with patch.object(tr.requests,'get',side_effect=request) as get:
            result=tr.translate_text(source,'ru','en')
            count=get.call_count
            self.assertEqual(tr.translate_text(source,'ru','en'),result)
            self.assertEqual(get.call_count,count)
        self.assertGreater(count,1)
        self.assertIn('\n\n',result)
        self.assertEqual(' '.join(' '.join(calls).split()),' '.join(source.split()))

    def test_quota_errors_and_untranslated_russian_fail_safely(self):
        for response in [self.response('LIMIT',403),self.response('Непереведённый текст'),Mock(status_code=503)]:
            with patch.object(tr.requests,'get',return_value=response):
                with self.assertRaises(ImportFailure):tr.translate_text('Товар','ru','en')

    def test_html_entities_are_decoded(self):
        with patch.object(tr.requests,'get',return_value=self.response('Email &amp; password')):
            self.assertEqual(tr.translate_text('Почта и пароль','ru','en'),'Email & password')
