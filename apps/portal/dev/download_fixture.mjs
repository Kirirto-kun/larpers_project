// Test-only HTTP download endpoint; application DTOs are intercepted per test.
import { createServer } from 'node:http';
createServer((req, res) => {
  if (req.url === '/health') { res.writeHead(200); res.end('test-downloads'); return; }
  if (req.url === '/api/v1/artifacts/artifact-1/content') {
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="result.txt"', 'Content-Length': '3' });
    res.end('OK\n'); return;
  }
  res.writeHead(404); res.end();
}).listen(18991, '127.0.0.1');
