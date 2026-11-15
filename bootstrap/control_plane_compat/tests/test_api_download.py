import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from threading import Thread
import unittest
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

class DownloadTest(unittest.TestCase):
 def test_explicit_gpu_download_bound_preserves_artifact_larger_than_mac_limit(self):
  sys.path.insert(0,str(ROOT))
  from run_mac import API
  content=b'x'*328000
  class Handler(BaseHTTPRequestHandler):
   def do_GET(self):
    self.send_response(200); self.end_headers(); self.wfile.write(content)
   def log_message(self,*args): pass
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=Thread(target=server.serve_forever,daemon=True); thread.start()
  try:
   api=API.__new__(API); api.token='isolated-fixture'
   api.endpoint=f'http://127.0.0.1:{server.server_port}'
   api.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   api.raw_max=1<<20
   actual,_=api.request('GET','/artifact',raw=True)
   self.assertEqual(actual,content)
   api.raw_max=65536
   actual,_=api.request('GET','/artifact',raw=True)
   self.assertEqual(len(actual),65537)
  finally:
   server.shutdown(); server.server_close(); thread.join(); sys.path.remove(str(ROOT))

if __name__=='__main__': unittest.main()
