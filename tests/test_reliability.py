import contextlib
import hashlib
import http.cookiejar
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as core


class BackupReliabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'dados'
        self.archive = self.root / 'reserva.zip'
        core.ensure_db(self.data)
        self.file_id = core.add_stream(self.data, 'guia.txt', io.BytesIO(b'guia'), 4)
        self.entry = f'objetos/{self.file_id}'

    def rewrite_archive(self, target, replace):
        with zipfile.ZipFile(self.archive) as source:
            manifest = json.loads(source.read('manifest.json'))
            entries = {name: source.read(name) for name in manifest['entries']}
        for name, content in replace.items():
            if content is None:
                entries.pop(name)
            else:
                entries[name] = content
        manifest['entries'] = {
            name: {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
            for name, content in entries.items()
        }
        with zipfile.ZipFile(target, 'w') as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
            archive.writestr('manifest.json', json.dumps(manifest))

    def test_backup_contains_only_catalog_objects_while_server_is_open(self):
        orphan = self.data / 'objetos' / ('f' * 32)
        orphan.write_bytes(b'fora do catalogo')
        with core.SurvivalHTTPServer(('127.0.0.1', 0), self.data):
            count, _ = core.backup_data(self.data, self.archive)
        self.assertEqual(count, 2)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(set(archive.namelist()), {'manifest.json', 'server.sqlite3', self.entry})
            self.assertEqual(archive.read(self.entry), b'guia')
        core.restore_data(self.data, self.archive)
        self.assertEqual(core.verify_data(self.data), (1, []))

    def test_backup_leaves_previous_archive_when_source_is_invalid(self):
        core.backup_data(self.data, self.archive)
        original_zip = self.archive.read_bytes()
        original_entries = set(self.root.iterdir())
        obj = self.data / self.entry
        for problem, content in [('hash', b'mapa'), ('tamanho', b'g'), ('ausente', None)]:
            with self.subTest(problem=problem):
                if content is None:
                    obj.unlink()
                else:
                    obj.write_bytes(content)
                with self.assertRaises(ValueError):
                    core.backup_data(self.data, self.archive)
                self.assertEqual(self.archive.read_bytes(), original_zip)
                self.assertEqual(set(self.root.iterdir()), original_entries)

    def test_backup_holds_catalog_steady_while_copying_objects(self):
        write_entry = core.write_backup_entry
        checked = []

        def check_lock(archive, path, name):
            if name == self.entry:
                with contextlib.closing(sqlite3.connect(core.data_paths(self.data)[0], timeout=0)) as writer:
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                        writer.execute('BEGIN IMMEDIATE')
                checked.append(name)
            return write_entry(archive, path, name)

        with mock.patch.object(core, 'write_backup_entry', side_effect=check_lock):
            core.backup_data(self.data, self.archive)
        self.assertEqual(checked, [self.entry])

    def test_restore_checks_catalog_before_switching_data(self):
        core.backup_data(self.data, self.archive)
        core.plan_update(self.data, 'meeting', 'Plano ativo')
        revised = self.root / 'reserva-revisada.zip'
        for problem, content in [('hash', b'mapa'), ('tamanho', b'g'), ('ausente', None)]:
            with self.subTest(problem=problem):
                self.rewrite_archive(revised, {self.entry: content})
                with self.assertRaises(ValueError):
                    core.restore_data(self.data, revised)
                self.assertEqual(core.plan_get(self.data)['meeting']['body'], 'Plano ativo')
                self.assertFalse(list(self.root.glob('dados.anterior_*')))
                self.assertEqual(core.verify_data(self.data), (1, []))

    def test_restore_accepts_extra_object_from_legacy_backup(self):
        core.backup_data(self.data, self.archive)
        legacy = self.root / 'reserva-antiga.zip'
        legacy_entry = 'objetos/' + 'f' * 32
        self.rewrite_archive(legacy, {legacy_entry: b'objeto antigo'})
        core.restore_data(self.data, legacy)
        self.assertEqual(core.verify_data(self.data), (1, []))

    def test_restore_upgrades_v01_schema_in_new_directory(self):
        core.add_user(self.data, 'familia', 'LeituraTeste123!', 'leitor')
        with contextlib.closing(core.connect(self.data)) as con:
            con.executescript('DROP TABLE emergency_plan; DROP TABLE audit;')
        core.backup_data(self.data, self.archive)
        recovered = self.root / 'nova-instalacao' / 'dados'
        self.assertEqual(core.main(['--data', str(recovered), 'restore', str(self.archive), '--yes']), 0)
        self.assertEqual(core.check_password(recovered, 'familia', 'LeituraTeste123!')['role'], 'leitor')
        self.assertEqual(core.plan_get(recovered)['contacts']['body'], '')
        self.assertEqual(core.verify_data(recovered), (1, []))

    def test_restore_refuses_server_open_in_another_process(self):
        core.backup_data(self.data, self.archive)
        core.plan_update(self.data, 'meeting', 'Plano ativo')
        command = [
            sys.executable, '-B', str(core.ROOT / 'server.py'),
            '--data', str(self.data), 'restore', str(self.archive), '--yes',
        ]
        with core.SurvivalHTTPServer(('127.0.0.1', 0), self.data):
            attempt = subprocess.run(command, capture_output=True, text=True, timeout=12)
            self.assertNotEqual(attempt.returncode, 0, attempt.stdout)
            self.assertEqual(core.plan_get(self.data)['meeting']['body'], 'Plano ativo')
        attempt = subprocess.run(command, capture_output=True, text=True, timeout=12)
        self.assertEqual(attempt.returncode, 0, attempt.stderr)
        self.assertEqual(core.plan_get(self.data)['meeting']['body'], '')


class SetupTests(unittest.TestCase):
    def test_launcher_initialization_resumes_after_invalid_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'dados'
            command = ['--data', str(data), 'init', '--if-needed', '--vazio']
            with mock.patch.object(core.getpass, 'getpass', side_effect=['curta', 'curta']):
                with self.assertRaises(ValueError):
                    core.main(command)
            self.assertTrue(core.data_paths(data)[0].is_file())
            with mock.patch.object(core.getpass, 'getpass', side_effect=['AdminTeste123!'] * 2):
                with mock.patch('builtins.input', return_value='n'):
                    self.assertEqual(core.main(command), 0)
            with mock.patch.object(core.getpass, 'getpass') as prompt:
                self.assertEqual(core.main(command), 0)
                prompt.assert_not_called()
            self.assertEqual(core.check_password(data, 'admin', 'AdminTeste123!')['role'], 'admin')


class LibraryPaginationTests(unittest.TestCase):
    def test_large_catalog_is_paginated_and_exported_in_full(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / 'dados'
            core.ensure_db(data)
            core.add_user(data, 'familia', 'Leitura!Senha123', 'leitor')
            count = 503
            digest = hashlib.sha256(b'a').hexdigest()
            with contextlib.closing(core.connect(data)) as con, con:
                con.executemany(
                    'INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    [
                        (f'{i:032x}', f'Guia {i}.txt', 'text/plain', 1, digest,
                         'Guias' if i < 501 else 'Mapas', '', 'água', 1)
                        for i in range(count)
                    ],
                )
            with core.SurvivalHTTPServer(('127.0.0.1', 0), data) as server:
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    self.check_catalog_api(server, count)
                finally:
                    server.shutdown()
                    worker.join(timeout=2)

    def check_catalog_api(self, server, count):
        base = f'http://127.0.0.1:{server.server_address[1]}'
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        credentials = json.dumps({'username': 'familia', 'password': 'Leitura!Senha123'}).encode()
        login = urllib.request.Request(base + '/api/login', credentials, {'Content-Type': 'application/json'})
        with opener.open(login, timeout=5) as response:
            self.assertEqual(response.status, 200)

        def get_json(path):
            with opener.open(base + path, timeout=5) as response:
                return json.load(response)

        ids = []
        for page in range(1, 12):
            result = get_json(f'/api/files?page={page}&page_size=50')
            self.assertEqual((result['total'], result['page'], result['page_size']), (count, page, 50))
            ids.extend(file['id'] for file in result['files'])
        self.assertEqual(ids, [f'{i:032x}' for i in reversed(range(count))])
        legacy = get_json('/api/files')
        self.assertEqual(list(legacy), ['files'])
        self.assertEqual([file['id'] for file in legacy['files']], ids[:500])
        manifest = get_json('/api/manifest')
        self.assertEqual(sorted(file['id'] for file in manifest['files']), sorted(ids))
        filters = urllib.parse.urlencode({'q': 'água', 'category': 'Guias', 'page': 11, 'page_size': 50})
        filtered = get_json('/api/files?' + filters)
        self.assertEqual(filtered['total'], 501)
        self.assertEqual([file['id'] for file in filtered['files']], [ids[-1]])
        empty = get_json('/api/files?page=12&page_size=50')
        self.assertEqual((empty['page'], empty['total'], empty['files']), (12, count, []))
