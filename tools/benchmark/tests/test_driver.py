import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('driver',Path(__file__).parents[1]/'run.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
class Response:
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.data).encode()
class Driver(unittest.TestCase):
    def test_collect_preserves_server_data_and_uses_http_only(self):
        payloads=[{'id':'b-1'},{'id':'b-1','state':'running'},{'id':'b-1','state':'succeeded'},{'records':[],'config':{'experiment_id':'b-1'}}]
        requests=[]
        def send(request,timeout):
            requests.append(request);return Response(payloads.pop(0))
        with TemporaryDirectory() as directory, patch.object(driver.urllib.request,'urlopen',send):
            result=driver.collect('http://127.0.0.1:8000',{'concurrency':2},Path(directory),token='not-a-real-token',interval=0)
            self.assertEqual(result['state'],'succeeded')
            self.assertEqual(requests[0].get_method(),'POST')
            self.assertTrue(all(r.get_method()=='GET' for r in requests[1:]))
            self.assertEqual(len((Path(directory)/'snapshots.jsonl').read_text().splitlines()),2)
            self.assertEqual(json.loads((Path(directory)/'raw.json').read_text())['records'],[])
            self.assertNotIn('not-a-real-token',''.join(f.read_text() for f in Path(directory).iterdir()))
    def test_timeout_keeps_id_and_does_not_resubmit(self):
        with TemporaryDirectory() as directory, patch.object(driver.urllib.request,'urlopen',side_effect=[Response({'id':'b-1'}),Response({'id':'b-1','state':'running'})]) as send:
            with self.assertRaises(TimeoutError):driver.collect('http://localhost:8000',{},Path(directory),timeout=0)
            self.assertEqual(send.call_count,2)
            self.assertEqual(json.loads((Path(directory)/'created.json').read_text())['id'],'b-1')
    def test_remote_http_and_url_credentials_rejected(self):
        for url in ['http://campus.example','https://user:password@example.com','file:///tmp/data']:
            with self.subTest(url=url),TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):driver.collect(url,{},Path(directory))
if __name__=='__main__':unittest.main()
