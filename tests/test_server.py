import contextlib
import http.cookiejar
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as core

class _DataTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'data'
        core.ensure_db(self.path)
        core.add_user(self.path, 'admin', 'Teste!Senha123', 'admin')
        core.add_user(self.path, 'familia', 'Familia!Senha123', 'leitor')

    def tearDown(self):
        self.tmp.cleanup()


class CoreTests(_DataTestCase):
    def test_network_announces_private_addresses_and_real_port(self):
        interfaces = ['192.168.1.9', '192.168.1.9', '10.0.0.4', '127.0.0.1', '0.0.0.0', '8.8.8.8']
        with mock.patch.object(core, 'local_ipv4_addresses', return_value=interfaces):
            access = core.access_info(('0.0.0.0', 9876), '10.0.0.4')
        self.assertTrue(access['lan_enabled'])
        self.assertEqual(access['access_urls'], [
            {'url': 'http://10.0.0.4:9876', 'kind': 'lan'},
            {'url': 'http://192.168.1.9:9876', 'kind': 'lan'},
            {'url': 'http://127.0.0.1:9876', 'kind': 'loopback'},
        ])

    def test_password_roles(self):
        self.assertIsNone(core.check_password(self.path,'admin','errada'))
        self.assertEqual(core.check_password(self.path,'familia','Familia!Senha123')['role'],'leitor')
        with self.assertRaises(ValueError): core.add_user(self.path, 'foo', 'curta', 'leitor')

    def test_import_search_verify(self):
        sample = Path(self.tmp.name) / 'guia.md'
        sample.write_text('Manual de água potável e planejamento familiar',encoding='utf-8')
        fid = core.import_file(self.path,sample,'Guias','Documentação essencial')
        self.assertEqual(len(core.list_files(self.path,'potável')),1)
        self.assertEqual(core.get_file(self.path,fid)['category'],'Guias')
        self.assertEqual(core.verify_data(self.path),(1,[]))
        (self.path/'objetos'/fid).write_text('corrompido')
        self.assertEqual(len(core.verify_data(self.path)[1]),1)

    def test_backup_restore(self):
        original = Path(self.tmp.name)/'mapa.bin'
        original.write_bytes(b'\x00\x01\x02\x03')
        fid = core.import_file(self.path,original,'Mapas')
        archive = Path(self.tmp.name)/'reserva.zip'
        count,_ = core.backup_data(self.path,archive)
        self.assertEqual(count,2)
        with zipfile.ZipFile(archive) as z:
            self.assertIn('manifest.json',z.namelist())
            self.assertIn('server.sqlite3',z.namelist())
        old = core.restore_data(self.path,archive)
        self.assertIsNotNone(old)
        self.assertEqual(core.get_file(self.path,fid)['size'],4)
        self.assertEqual(core.verify_data(self.path),(1,[]))

    def test_backup_rejects_tampered_content(self):
        source=Path(self.tmp.name)/'notes.txt'
        source.write_text('Dados originais',encoding='utf-8')
        core.import_file(self.path,source,'Documentos')
        archive=Path(self.tmp.name)/'backup.zip'
        tampered=Path(self.tmp.name)/'alterado.zip'
        core.backup_data(self.path,archive)
        with zipfile.ZipFile(archive) as original, zipfile.ZipFile(tampered,'w') as edited:
            for name in original.namelist():
                content=original.read(name)
                if name.startswith('objetos/'):
                    content=b'CONTEUDO ALTERADO' + content
                edited.writestr(name,content)
        with self.assertRaises(ValueError):core.restore_data(self.path,tampered)
        self.assertEqual(len(core.list_files(self.path)),1)

    def test_invalid_paths_metadata(self):
        self.assertEqual(core.safe_filename('../arquivo.pdf'),'arquivo.pdf')
        with self.assertRaises(ValueError): core.safe_filename('...')
        with self.assertRaises(ValueError): core.sanitize_metadata('invasores','')

class HttpTests(_DataTestCase):
    def setUp(self):
        super().setUp()
        self.srv = core.SurvivalHTTPServer(('127.0.0.1',0),self.path,4)
        self.thread = threading.Thread(target=self.srv.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.srv.server_address[1]}'
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=2)
        super().tearDown()

    def request(self, path, method='GET', payload=None, headers=None, check=True):
        headers=headers or {}
        if self.csrf and method!='GET': headers['X-CSRF-Token']=self.csrf
        if isinstance(payload,dict): payload=json.dumps(payload).encode();headers['Content-Type']='application/json'
        req=urllib.request.Request(self.base+path,data=payload,headers=headers,method=method)
        try:
            res=self.opener.open(req,timeout=5)
        except urllib.error.HTTPError as e:
            if check: raise
            return e.code, json.loads(e.read())
        data=res.read()
        return res.status, (json.loads(data) if res.headers.get_content_type()=='application/json' else data)

    def login(self, who='admin', password='Teste!Senha123'):
        code,_=self.request('/api/login','POST',{'username':who,'password':password})
        self.assertEqual(code,200)
        self.csrf=self.request('/api/me')[1]['session']['csrf']

    def test_login_permissions_upload_stream_range(self):
        code,obj=self.request('/api/stats',check=False)
        self.assertEqual(code,401)
        code,obj=self.request('/api/login','POST',{'username':'admin','password':'errada'},check=False)
        self.assertEqual(code,401)
        self.login()
        data=b'0123456789' * 10
        code,obj=self.request('/api/files/upload?filename=teste.txt&category=Guias&description=Teste','POST',data)
        self.assertEqual(code,201)
        fid=obj['id']
        self.assertEqual(self.request('/api/files?q=01234')[1]['files'][0]['id'],fid)
        self.assertEqual(self.request('/api/files/'+fid+'/download')[1],data)
        req=urllib.request.Request(self.base+'/api/files/'+fid+'/view',headers={'Range':'bytes=1-4'})
        with self.opener.open(req) as response:
            self.assertEqual(response.status,206)
            self.assertEqual(response.read(),b'1234')
        self.assertEqual(self.request('/api/files/'+fid+'/metadata','PUT',{'category':'Manuais','description':'Revisado'})[0],200)
        self.assertEqual(self.request('/api/files/'+fid,'DELETE')[0],200)
        self.assertEqual(self.request('/api/files/'+fid+'/download',check=False)[0],404)

    def test_unauthorized_download_and_static_assets(self):
        p=Path(self.tmp.name)/'publico.txt'
        p.write_text('Biblioteca local',encoding='utf-8')
        fid=core.import_file(self.path,p,'Guias')
        self.assertEqual(self.request('/api/files/'+fid+'/download',check=False)[0],401)
        for path in ('/','/app.css','/app.js','/api/health'):
            self.assertEqual(self.request(path)[0],200)
        self.login('familia','Familia!Senha123')
        self.assertEqual(self.request('/api/files/'+fid+'/download')[1],b'Biblioteca local')

    def test_reader_no_upload_and_csrf(self):
        self.login('familia','Familia!Senha123')
        self.assertEqual(self.request('/api/files/upload?filename=x.txt','POST',b'a',check=False)[0],403)
        self.assertEqual(self.request('/api/stats')[0],200)
        self.csrf='errado'
        self.assertEqual(self.request('/api/logout','POST',check=False)[0],403)

    def test_access_requires_login_and_distinguishes_loopback(self):
        self.assertEqual(self.request('/api/access', check=False)[0], 401)
        self.login('familia', 'Familia!Senha123')
        status, access = self.request('/api/access')
        self.assertEqual(status, 200)
        self.assertEqual(access, {
            'lan_enabled': False,
            'scheme': 'http',
            'port': self.srv.server_port,
            'access_urls': [{'url': self.base, 'kind': 'loopback'}],
        })

    def test_origin_and_host(self):
        self.login()
        code,_=self.request('/api/logout','POST',headers={'Origin':'http://site-malicioso.example'},check=False)
        self.assertEqual(code,403)
        self.assertEqual(self.request('/')[0],200)

if __name__ == '__main__':
    unittest.main(verbosity=2)
