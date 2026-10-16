import configparser
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from admin.main import app
from admin import auth


class AdminLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        stock_patch = patch('admin.services.offer_stock.DB_PATH', folder / 'stock.sqlite3')
        stock_patch.start()
        self.addCleanup(stock_patch.stop)
        self.config_path = folder / 'admin.cfg'
        for name, value in [('ADMIN_CFG', str(self.config_path)),
                            ('SESSION_DB', folder / 'sessions.sqlite3'),
                            ('CSRF_TOKEN_FILE', str(folder / 'csrf.txt'))]:
            p = patch.object(auth, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.write_credentials('owner', 'secret%password')
        self.client = TestClient(app, follow_redirects=False)

    def write_credentials(self, username, password):
        cfg = configparser.ConfigParser(interpolation=None)
        cfg['admin'] = dict(username=username, password=password)
        with self.config_path.open('w') as f:
            cfg.write(f)

    def sign_in(self, password='secret%password', next='/lots/'):
        page = self.client.get('/login', params={'next': next})
        self.assertEqual(page.status_code, 200)
        return self.client.post('/login', data={
            'username': 'owner', 'password': password, 'next': next,
            'csrf_token': self.client.cookies.get(auth.LOGIN_CSRF_COOKIE)})

    def test_protected_pages_redirect_without_basic_auth_challenge(self):
        for path in ['/', '/lots/', '/settings/', '/providers/', '/logs/', '/status/', '/imports/']:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers['location'].startswith('/login?next='))
                self.assertNotIn('www-authenticate', response.headers)

    def test_form_is_public_and_does_not_expose_password(self):
        response = self.client.get('/login')
        self.assertIn('type="password"', response.text)
        self.assertNotIn('secret%password', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('HttpOnly', response.headers['set-cookie'])

    def test_existing_credentials_create_session_and_return_to_page(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/lots/')
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=strict', cookie)
        self.assertIn('Max-Age=28800', cookie)
        self.assertEqual(self.client.get('/imports/').status_code, 200)
        self.assertEqual(self.client.get('/login').status_code, 303)

    def test_wrong_credentials_do_not_authenticate(self):
        response = self.sign_in(password='wrong')
        self.assertEqual(response.status_code, 401)
        self.assertIn('Incorrect username or password', response.text)
        self.assertNotIn('www-authenticate', response.headers)
        self.assertIsNone(self.client.cookies.get(auth.SESSION_COOKIE))

    def test_login_csrf_and_cross_site_posts_are_rejected(self):
        response = self.client.post('/login', data={'username': 'owner', 'password': 'secret%password'})
        self.assertEqual(response.status_code, 403)
        self.client.get('/login')
        response = self.client.post('/login', data={
            'username': 'owner', 'password': 'secret%password',
            'csrf_token': self.client.cookies.get(auth.LOGIN_CSRF_COOKIE)},
            headers={'origin': 'https://attacker.example'})
        self.assertEqual(response.status_code, 403)

    def test_unicode_csrf_is_rejected_without_server_error(self):
        self.client.get('/login')
        response = self.client.post('/login', data={
            'username': 'owner', 'password': 'secret%password', 'csrf_token': 'é'})
        self.assertEqual(response.status_code, 403)

    def test_external_redirects_are_not_allowed(self):
        from admin.routes.login import destination
        for value in ['https://evil.example', '//evil.example', '/\\evil.example', '/\nevil']:
            self.assertEqual(destination(value), '/')
        self.assertEqual(self.sign_in(next='https://evil.example').headers['location'], '/')

    def test_logout_revokes_session_and_requires_csrf(self):
        self.sign_in()
        token = self.client.cookies.get(auth.SESSION_COOKIE)
        # A GET confirms logout without terminating a session.
        self.assertEqual(self.client.get('/logout').status_code, 200)
        self.assertEqual(auth.session_user(token), 'owner')
        self.assertEqual(self.client.post('/logout', data={'csrf_token': 'bad'}).status_code, 403)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': auth.get_csrf_token()}).status_code, 303)
        self.assertIsNone(auth.session_user(token))
        self.assertIsNone(self.client.cookies.get(auth.SESSION_COOKIE))

    def test_expiry_and_credentials_changes_revoke_sessions(self):
        self.sign_in()
        token = self.client.cookies.get(auth.SESSION_COOKIE)
        self.write_credentials('owner', 'new-password')
        self.assertIsNone(auth.session_user(token))
        self.write_credentials('owner', 'secret%password')
        with auth.session_database() as db:
            db.execute('UPDATE sessions SET expires = 0')
        self.assertIsNone(auth.session_user(token))

    def test_tampered_session_and_basic_header_cannot_authenticate(self):
        self.client.cookies.set(auth.SESSION_COOKIE, 'invented-token')
        self.assertEqual(self.client.get('/imports/').status_code, 303)
        self.assertEqual(self.client.get('/imports/', auth=('owner', 'secret%password')).status_code, 303)

    def test_session_database_stores_only_token_hash_and_survives_new_client(self):
        self.sign_in()
        token = self.client.cookies.get(auth.SESSION_COOKIE)
        with auth.session_database() as db:
            row = db.execute('SELECT * FROM sessions').fetchone()
        self.assertEqual(row[0], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, str(row))
        self.assertNotIn('secret%password', str(row))
        with TestClient(app, follow_redirects=False) as other:
            other.cookies.set(auth.SESSION_COOKIE, token)
            self.assertEqual(other.get('/imports/').status_code, 200)

    def test_https_sets_secure_cookies(self):
        with TestClient(app, base_url='https://testserver', follow_redirects=False) as client:
            self.assertIn('Secure', client.get('/login').headers['set-cookie'])
            response = client.post('/login', data={'username': 'owner', 'password': 'secret%password',
                'csrf_token': client.cookies.get(auth.LOGIN_CSRF_COOKIE)}, headers={'origin': 'https://testserver'})
            self.assertEqual(response.status_code, 303)
            self.assertIn('Secure', response.headers['set-cookie'])


if __name__ == '__main__':
    unittest.main()
