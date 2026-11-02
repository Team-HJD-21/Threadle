import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from connect_web import Onboarding, page

class InterfaceDeliveryTests(unittest.TestCase):
    def request(self,path):
        headers={}
        return SimpleNamespace(path=path,wfile=io.BytesIO(),headers_sent=headers,send_response=lambda code:None,send_header=lambda key,value:headers.update({key:value}),end_headers=lambda:None)

    def test_shared_controls_assets_are_served_with_correct_mime(self):
        web=Onboarding.__new__(Onboarding)
        for name,mime in [('controls.js','text/javascript'),('controls.css','text/css')]:
            request=self.request('/connect/assets/'+name)
            web.asset(request,request.path)
            self.assertTrue(request.headers_sent['Content-Type'].startswith(mime))
            self.assertEqual(request.wfile.getvalue(),(Path(__file__).parent/'web_assets'/name).read_bytes())

    def test_common_page_and_policy_load_same_origin_controls(self):
        web=Onboarding.__new__(Onboarding)
        for path in ['/connect','/connect/repositories','/connect/team']:
            request=self.request(path)
            html=page('Test','<select name="repo"><option>one</option></select>')
            web.send(request,200,html)
            self.assertIn("script-src 'self'",request.headers_sent['Content-Security-Policy'])
            self.assertIn('controls.js?v=1',request.wfile.getvalue().decode())
            self.assertIn('controls.css?v=20261102-2',request.wfile.getvalue().decode())
