import contextlib, http.cookiejar, json, tempfile, threading, unittest
import urllib.error, urllib.request, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as ss

class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.data=Path(self.tmp.name)/'db'
        ss.ensure_db(self.data)
        ss.add_user(self.data,'admin','SuperSenha123!','admin')
        ss.add_user(self.data,'familia','LeitorSenha123!','leitor')
        self.server=ss.SurvivalHTTPServer(('127.0.0.1',0),self.data,4)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=''
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3);self.tmp.cleanup()
    def req(self,url,method='GET',json_body=None,check=True,csrf=True):
        headers={}
        if method!='GET' and self.csrf and csrf:headers['X-CSRF-Token']=self.csrf
        data=None
        if json_body is not None:data=json.dumps(json_body).encode();headers['Content-Type']='application/json'
        rq=urllib.request.Request(self.url+url,data=data,headers=headers,method=method)
        try:r=self.client.open(rq,timeout=5)
        except urllib.error.HTTPError as e:
            if check:raise
            return e.code,json.loads(e.read())
        with r:return r.status,json.loads(r.read())
    def login(self,name,pw):
        self.req('/api/login','POST',{'username':name,'password':pw})
        self.csrf=self.req('/api/me')[1]['session']['csrf']
    def test_plan_initial_and_edit(self):
        self.login('admin','SuperSenha123!')
        self.assertEqual(len(self.req('/api/emergency')[1]['plan']),5)
        status,out=self.req('/api/emergency/contacts','PUT',{'body':'Policia 190\nPonto seguro: casa familiar'})
        self.assertEqual(status,200)
        self.assertIn('Ponto seguro',self.req('/api/emergency')[1]['plan']['contacts']['body'])
        self.assertEqual(self.req('/api/audit')[1]['events'][0]['action'],'plan_change')
    def test_reader_cannot_edit_or_diagnose(self):
        self.login('familia','LeitorSenha123!')
        self.assertEqual(self.req('/api/emergency/departure','PUT',{'body':'rua'},False)[0],403)
        self.assertEqual(self.req('/api/diagnostics',check=False)[0],403)
        self.assertEqual(self.req('/api/verify','POST',{},False)[0],403)
        self.assertEqual(self.req('/api/audit',check=False)[0],403)
        self.assertEqual(self.req('/api/emergency')[0],200)
    def test_csrf_and_invalid_plan(self):
        self.login('admin','SuperSenha123!')
        self.assertEqual(self.req('/api/emergency/meeting','PUT',{'body':'local'},False,False)[0],403)
        self.assertEqual(self.req('/api/emergency/bad','PUT',{'body':'x'},False)[0],400)
        self.assertEqual(self.req('/api/emergency/contacts','PUT',{'body':'x'*4001},False)[0],400)
    def test_diagnostic_and_integrity(self):
        self.login('admin','SuperSenha123!')
        self.assertEqual(self.req('/api/diagnostics')[1]['sqlite_integrity'],'ok')
        self.assertTrue(self.req('/api/verify','POST',{})[1]['ok'])
        self.assertEqual(self.req('/api/manifest')[1]['format'],'survival-server-manifest-v1')
    def test_migration_preserves_files(self):
        self.login('admin','SuperSenha123!')
        p=Path(self.tmp.name)/'amostra.txt';p.write_text('persistente')
        ident=ss.import_file(self.data,p)
        ss.ensure_db(self.data)  # additive migration is safe
        self.assertEqual(ss.get_file(self.data,ident)['filename'],'amostra.txt')
        ss.plan_update(self.data,'meeting','ponto central')
        self.assertEqual(ss.plan_get(self.data)['meeting']['body'],'ponto central')

if __name__=='__main__':unittest.main(verbosity=2)
