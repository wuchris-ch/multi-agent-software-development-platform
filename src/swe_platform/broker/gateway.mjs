// Trusted host process. Configuration arrives through an anonymous stdin pipe.
import fs from 'node:fs';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
try {
  const response = await fetch(input.url, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(input.timeout_ms),
    headers: {'content-type': 'application/json', authorization: `Bearer ${input.key}`},
    body: JSON.stringify(input.body),
  });
  if (!response.ok) {
    await response.body?.cancel();
    process.stdout.write(JSON.stringify({status: response.status, error: 'model request failed'}));
  } else {
    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > 4 * 1024 * 1024) { await reader.cancel(); throw new Error('response limit'); }
      chunks.push(Buffer.from(value));
    }
    process.stdout.write(JSON.stringify({status: response.status,
      content_type: response.headers.get('content-type') || 'application/json',
      body: Buffer.concat(chunks).toString('base64')}));
  }
} catch {
  process.stdout.write(JSON.stringify({status: 502, error: 'model transport unavailable'}));
}
