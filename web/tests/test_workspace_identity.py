import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from werkzeug.test import Client
from werkzeug.wrappers import Response
from web.app import create_app
from web.demo import DemoPlatform
from web.workspace_identity import WorkspaceIdentity,IdentityUnavailable

class IdentityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.services=[]
  def factory(path):
   app=create_app(path,platform=DemoPlatform(),auth=True,public_origin='https://workspace.example');self.services.append(app.extensions['workspace']);return app
  self.app=factory(self.root);self.s=self.services[0]
  self.verify=Mock(side_effect=lambda token:{'id':int(token),'username':'user-'+token,'app_access':{'bilibiliapis':True}} if token in ('1','2') else None)
  self.gate=WorkspaceIdentity(self.app,self.root,'bilibili',factory,1,verify=self.verify,origin='https://workspace.example',machine_key='synthetic-machine-key')
  self.app.wsgi_app=self.gate;self.client=Client(self.app,Response)
 def tearDown(self):
  for s in self.services:s.pool.shutdown(wait=True)
  self.tmp.cleanup()
 def login(self,uid):self.client.set_cookie('ops_workspace_session',str(uid))
 def post(self,path,data):return self.client.post(path,json=data,headers={'Origin':'https://workspace.example','X-App-Request':'1'})
 def test_no_password_or_header_bypass(self):
  self.assertEqual(self.client.get('/api/drafts',headers={'X-User-ID':'1','X-Account-ID':'demo-10001'}).status_code,401)
  self.assertEqual(self.client.get('/').status_code,302)
  self.assertEqual(self.post('/api/login',{'password':'irrelevant'}).status_code,401)
 def test_two_users_same_platform_id_separate_drafts(self):
  self.login(1);r=self.post('/api/drafts',{'title':'only-owner','text':'private'});self.assertEqual(r.status_code,200)
  original=self.client.get('/api/drafts').json['items'][0]['id']
  self.login(2);self.assertEqual(self.client.get('/api/drafts').json['items'],[])
  self.assertEqual(self.post('/api/drafts',{'title':'second','text':'other'}).status_code,200)
  self.assertEqual(self.client.get('/api/workspace-identity').json['owner']['id'],2)
  self.login(1);items=self.client.get('/api/drafts').json['items'];self.assertEqual(len(items),1);self.assertEqual(items[0]['id'],original)
 def test_forged_platform_account_does_not_change_tenant(self):
  self.login(2);self.assertEqual(self.client.get('/api/workspace-identity',headers={'X-User-ID':'1'}).json['owner']['id'],2)
 def test_revocation_ignores_stale_local_session(self):
  self.login(1);self.assertEqual(self.client.get('/api/meta').status_code,200);self.verify.side_effect=lambda _:None;self.assertEqual(self.client.get('/api/meta').status_code,401)
 def test_application_revoke_preserves_identity_but_blocks_business_data(self):
  self.login(2);self.assertEqual(self.client.get('/api/meta').status_code,200)
  self.verify.side_effect=lambda _: {'id':2,'username':'user-2','app_access':{'bilibiliapis':False}}
  self.assertEqual(self.client.get('/api/meta').status_code,403)
  response=self.client.get('/');self.assertEqual(response.status_code,302)
  self.assertIn('/permissions?app=bilibiliapis',response.headers['Location'])
 def test_missing_permission_claims_fail_closed(self):
  self.login(1);self.verify.side_effect=lambda _: {'id':1,'username':'user-1'}
  self.assertEqual(self.client.get('/api/meta').status_code,403)
 def test_outage_fails_closed(self):
  self.login(1);self.verify.side_effect=IdentityUnavailable();self.assertEqual(self.client.get('/api/meta').status_code,503)
 def test_origin_still_required(self):
  self.login(1);self.assertEqual(self.client.post('/api/drafts',json={'title':'x','text':'x'},headers={'Origin':'https://evil.example','X-App-Request':'1'}).status_code,403)
 def test_machine_auth_is_explicit(self):
  self.assertEqual(self.client.get('/api/meta',headers={'X-Ops-Machine-Key':'wrong'}).status_code,401)
  self.assertEqual(self.client.get('/api/meta',headers={'X-Ops-Machine-Key':'synthetic-machine-key'}).status_code,200)
 def test_new_tenant_has_separate_storage_path(self):
  self.login(2);self.client.get('/api/meta');self.assertTrue((self.root/'users/2/workspace.sqlite3').exists());self.assertNotEqual(self.gate.apps[2].extensions['workspace'].db_path,self.s.db_path)
 def test_logout_cross_origin_rejected(self):
  self.login(1);self.assertEqual(self.client.post('/api/logout',json={},headers={'Origin':'https://evil.example','X-App-Request':'1'}).status_code,403)
 def test_owner_only_tool_rejects_other_users(self):
  self.gate.owner_only=True;self.login(2);self.assertEqual(self.client.get('/api/meta').status_code,403);self.assertFalse(self.gate.apps)
if __name__=='__main__':unittest.main()
