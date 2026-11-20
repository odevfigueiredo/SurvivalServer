import concurrent.futures
import http.cookiejar
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as core


class ApiClient:
    def __init__(self, base):
        self.base = base
        self.csrf = None
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method='GET', body=None, *, csrf=True):
        headers = {}
        payload = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            payload = json.dumps(body).encode()
        if csrf and self.csrf and method != 'GET':
            headers['X-CSRF-Token'] = self.csrf
        request = urllib.request.Request(self.base + path, payload, headers, method=method)
        try:
            response = self.opener.open(request, timeout=8)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.last_headers = response.headers
            return response.status, json.load(response)

    def login(self, username, password):
        status, _ = self.request('/api/login', 'POST', {'username': username, 'password': password})
        if status == 200:
            self.csrf = self.request('/api/me')[1]['session']['csrf']
        return status


class UsersApiTests(unittest.TestCase):
    password = 'Senha!Teste123'
    new_password = 'NovaSenha!123'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name) / 'dados'
        core.ensure_db(self.data)
        core.add_user(self.data, 'admin', self.password, 'admin')
        core.add_user(self.data, 'familia', self.password)
        server = core.SurvivalHTTPServer(('127.0.0.1', 0), self.data)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.base = f'http://127.0.0.1:{server.server_address[1]}'
        self.admin = self.client('admin')

    def client(self, username=None):
        client = ApiClient(self.base)
        if username:
            self.assertEqual(client.login(username, self.password), 200)
        return client

    def test_permissions_csrf_and_public_account_fields(self):
        self.assertEqual(self.client().request('/api/users')[0], 401)
        reader = self.client('familia')
        new = {'username': 'apoio', 'password': self.password, 'role': 'admin'}
        self.assertEqual(reader.request('/api/users')[0], 403)
        self.assertEqual(reader.request('/api/users', 'POST', new)[0], 403)
        self.assertEqual(self.admin.request('/api/users', 'POST', new, csrf=False)[0], 403)
        self.assertEqual(self.admin.request('/api/users/familia', 'DELETE', csrf=False)[0], 403)
        self.assertEqual(self.admin.request('/api/users/familia/password', 'PUT',
                                            {'password': self.new_password}, csrf=False)[0], 403)
        status, result = self.admin.request('/api/users')
        self.assertEqual(status, 200)
        self.assertEqual(result['users'], [
            {'username': 'admin', 'role': 'admin', 'created_at': mock.ANY},
            {'username': 'familia', 'role': 'leitor', 'created_at': mock.ANY},
        ])

    def test_create_validate_and_delete_account(self):
        new = {'username': 'apoio', 'password': self.password, 'role': 'admin'}
        for field, invalid in [('username', 'usuário'), ('password', 'curta'), ('role', 'gerente')]:
            with self.subTest(field=field):
                self.assertEqual(self.admin.request('/api/users', 'POST', {**new, field: invalid})[0], 400)
        self.assertEqual(self.admin.request('/api/users', 'POST', new), (201, {'ok': True}))
        self.assertEqual(self.admin.request('/api/users', 'POST', new)[0], 409)
        self.assertEqual(self.admin.request('/api/users/ausente/password', 'PUT', {'password': self.password})[0], 404)
        secondary = self.client('apoio')
        self.assertEqual(secondary.request('/api/users/apoio', 'DELETE')[0], 409)
        self.assertEqual(self.admin.request('/api/users/apoio', 'DELETE'), (200, {'ok': True}))
        self.assertEqual(self.admin.request('/api/users/apoio', 'DELETE')[0], 404)
        self.assertEqual(self.admin.request('/api/users/admin', 'DELETE')[0], 409)
        self.assertEqual(secondary.request('/api/users')[0], 401)
        self.assertEqual(secondary.login('apoio', self.password), 401)
        events = self.admin.request('/api/audit')[1]['events']
        user_events = [(event['username'], event['action'], event['detail']) for event in events
                       if event['action'].startswith('user_')]
        self.assertEqual(user_events, [('admin', 'user_delete', 'apoio'), ('admin', 'user_create', 'apoio')])

    def test_password_change_revokes_target_sessions_and_clears_own_cookie(self):
        readers = [self.client('familia'), self.client('familia')]
        body = {'password': self.new_password}
        self.assertEqual(self.admin.request('/api/users/familia/password', 'PUT', body),
                         (200, {'ok': True, 'reauthenticate': False}))
        for reader in readers:
            self.assertEqual(reader.request('/api/stats')[0], 401)
        self.assertEqual(readers[0].login('familia', self.password), 401)
        self.assertEqual(readers[0].login('familia', self.new_password), 200)
        self.assertEqual(self.admin.request('/api/users')[0], 200)
        other_admin_session = self.client('admin')
        self.assertEqual(self.admin.request('/api/users/admin/password', 'PUT', body),
                         (200, {'ok': True, 'reauthenticate': True}))
        self.assertIn('Max-Age=0', self.admin.last_headers['Set-Cookie'])
        self.assertEqual(self.admin.request('/api/users')[0], 401)
        self.assertEqual(other_admin_session.request('/api/users')[0], 401)
        self.assertEqual(readers[0].request('/api/stats')[0], 200)
        self.assertEqual(self.admin.login('admin', self.new_password), 200)
        event = next(event for event in self.admin.request('/api/audit')[1]['events']
                     if event['action'] == 'user_password')
        self.assertEqual((event['username'], event['detail']), ('admin', 'admin'))

    def test_login_in_progress_cannot_reissue_session_after_password_change(self):
        checked, resume = threading.Event(), threading.Event()
        verify = core.verified_user

        def pause_verification(con, username, password):
            user = verify(con, username, password)
            if username == 'familia' and user:
                checked.set()
                if not resume.wait(5):
                    raise TimeoutError('A operação de troca de senha não terminou')
            return user

        reader = self.client()
        with mock.patch.object(core, 'verified_user', side_effect=pause_verification):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                login = executor.submit(reader.login, 'familia', self.password)
                try:
                    self.assertTrue(checked.wait(5), 'O login não chegou à verificação da senha')
                    status, _ = self.admin.request('/api/users/familia/password', 'PUT', {'password': self.new_password})
                    self.assertEqual(status, 200)
                finally:
                    resume.set()
                self.assertEqual(login.result(timeout=5), 401)
        self.assertEqual(reader.request('/api/me')[1], {'session': None})
        self.assertEqual(reader.login('familia', self.new_password), 200)

    def test_concurrent_admin_deletion_preserves_admin_account(self):
        core.add_user(self.data, 'apoio', self.password, 'admin')
        secondary = self.client('apoio')
        start = threading.Barrier(2, timeout=5)

        def delete(actor, target):
            start.wait()
            return actor.request('/api/users/' + target, 'DELETE')[0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            admin_delete = executor.submit(delete, self.admin, 'apoio')
            support_delete = executor.submit(delete, secondary, 'admin')
            self.assertEqual(sorted([admin_delete.result(timeout=8), support_delete.result(timeout=8)]), [200, 401])
            winner = self.admin if admin_delete.result() == 200 else secondary
        users = winner.request('/api/users')[1]['users']
        self.assertEqual(sum(user['role'] == 'admin' for user in users), 1)
