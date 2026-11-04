import assert from 'node:assert/strict';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync } from 'node:fs';
import { Readable } from 'node:stream';
import { createInterface } from 'node:readline';
import { test } from 'node:test';
import { createAssistantMessageEventStream } from '@mariozechner/pi-ai';
import { createPiServer, quota } from './server.mjs';

const token = 'synthetic-service-secret';
const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
const call = (name = 'submit_draft', args = { statement: '合成建议' }, id = 'call-1') =>
  ({ type: 'toolCall', id, name, arguments: args });
const body = () => ({ version: 1, run_id: randomUUID(), mode: 'draft',
  model: { provider: 'synthetic', api: 'openai-completions', id: 'test', baseUrl: 'http://127.0.0.1:1/v1', api_key: 'synthetic-api-key' },
  system_prompt: 'synthetic-system-prompt', input: 'synthetic-private-evidence',
  tools: [{ name: 'submit_draft', description: 'draft', parameters: { type: 'object',
    additionalProperties: false, properties: { statement: { type: 'string' } }, required: ['statement'] } }] });

function fake(content = (context => context.messages.some(m => m.role === 'toolResult') ? [] : [call()]), observe = () => {}) {
  return (model, context, options) => {
    observe(context, options);
    const stream = createAssistantMessageEventStream();
    const blocks = content(context);
    const message = { role: 'assistant', api: model.api, provider: model.provider, model: model.id,
      content: blocks, stopReason: blocks.some(b => b.type === 'toolCall') ? 'toolUse' : 'stop', timestamp: Date.now(),
      usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
    stream.push({ type: 'done', reason: message.stopReason, message });
    stream.end();
    return stream;
  };
}

async function setup(t, options = {}) {
  const logs = [];
  const server = createPiServer({ token, streamFn: fake(), log: entry => logs.push(entry), ...options });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const url = `http://127.0.0.1:${server.address().port}`;
  const request = (path, method = 'GET', data, headers = auth) => fetch(url + path,
    { method, headers, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
  async function start(data = body()) {
    const response = await request('/v1/turns', 'POST', data);
    assert.equal(response.status, 200);
    const lines = createInterface({ input: Readable.fromWeb(response.body), crlfDelay: Infinity })[Symbol.asyncIterator]();
    return { data, next: async () => JSON.parse((await lines.next()).value),
      result: (id, result = {}, is_error = false) => request(`/v1/turns/${data.run_id}/results/${id}`, 'POST', { result, is_error }),
      cancel: () => request(`/v1/turns/${data.run_id}`, 'DELETE'), response };
  }
  return { request, start, logs };
}

test('every route authenticates; bad inputs and unsupported modes/APIs are rejected', async t => {
  const { request } = await setup(t);
  for (const headers of [{}, { Authorization: 'Bearer incorrect' }]) {
    for (const [path, method] of [['/health', 'GET'], ['/v1/turns', 'POST'],
      [`/v1/turns/${randomUUID()}`, 'DELETE'], [`/v1/turns/${randomUUID()}/results/1`, 'POST']])
      assert.equal((await request(path, method, undefined, headers)).status, 401);
  }
  assert.equal((await request('/health')).status, 200);
  for (const patch of [{ mode: 'chat' }, { callback_url: 'http://db' }, { version: 2 },
    { run_id: 'not-a-uuid' }, { model: { ...body().model, api: 'openai-codex-responses' } },
    { model: { ...body().model, baseUrl: 'file:///etc/passwd' } }])
    assert.equal((await request('/v1/turns', 'POST', { ...body(), ...patch })).status, 400);
});

test('tool results bind to the waiting call and run; duplicate IDs/results do not disturb it', async t => {
  const { request, start } = await setup(t, { streamFn: fake(context => context.messages.some(m => m.role === 'toolResult')
    ? [] : [call('submit_draft', { statement: 'ok' }, context.messages[0].content[0].text)]) });
  const a = await start({ ...body(), input: 'call-a' });
  const b = await start({ ...body(), input: 'call-b' });
  const ca = await a.next(); const cb = await b.next();
  assert.equal((await request('/v1/turns', 'POST', a.data)).status, 400);
  assert.equal((await a.result('unknown')).status, 409);
  assert.equal((await a.result(cb.call_id)).status, 409);
  assert.equal((await request(`/v1/turns/${randomUUID()}/results/${ca.call_id}`, 'POST', { result: {}, is_error: false })).status, 409);
  assert.equal((await a.result(ca.call_id)).status, 200);
  assert.equal((await a.next()).type, 'done');
  assert.equal((await a.result(ca.call_id)).status, 409);
  assert.equal((await b.result(cb.call_id)).status, 200);
  assert.equal((await b.next()).tool_calls, 1);
});

test('timeout, DELETE, and disconnect abort pending tools and release all capacity', async t => {
  let aborts = 0;
  const { start, request } = await setup(t, { deadlineMs: 250,
    streamFn: fake(undefined, (_ctx, options) => options.signal.addEventListener('abort', () => aborts++, { once: true })) });
  const a = await start(); await a.next();
  assert.equal((await a.next()).code, 'deadline');
  const b = await start(); const cb = await b.next();
  assert.equal((await b.cancel()).status, 200);
  assert.equal((await b.next()).code, 'cancelled');
  assert.equal((await b.result(cb.call_id)).status, 409);
  const controller = new AbortController();
  const c = await fetch((new URL('/v1/turns', a.response.url)), { method: 'POST', headers: auth, body: JSON.stringify(body()), signal: controller.signal });
  await c.body.getReader().read();
  controller.abort();
  // A subsequent HTTP round trip observes the disconnected socket's close event.
  await request('/health');
  assert.equal(aborts, 3);
  const d = await start(); const cd = await d.next();
  await d.result(cd.call_id); assert.equal((await d.next()).type, 'done');
});

test('fifth concurrent run is busy; cancelled runs release their slots', async t => {
  const { start, request } = await setup(t);
  const runs = [];
  for (let i = 0; i < 4; i++) { const run = await start(); await run.next(); runs.push(run); }
  const busy = await request('/v1/turns', 'POST', body());
  assert.equal(busy.status, 503); assert.equal((await busy.json()).code, 'busy');
  for (const run of runs) { await run.cancel(); await run.next(); }
  const fresh = await start(); const call = await fresh.next();
  await fresh.result(call.call_id); assert.equal((await fresh.next()).type, 'done');
});

test('eight model calls and sixteen tool executions are hard limits', async t => {
  let calls = 0;
  const first = await setup(t, { streamFn: fake(() => [call('unknown')], () => calls++) });
  const a = await first.start(); assert.equal((await a.next()).code, 'limit_exceeded');
  assert.equal(calls, 8);
  const second = await setup(t, { streamFn: fake(() => Array.from({ length: 17 }, (_, i) => call('submit_draft', { statement: 'ok' }, `call-${i}`))) });
  const b = await second.start();
  for (let i = 0; i < 16; i++) { const c = await b.next(); assert.equal(c.type, 'tool_call'); await b.result(c.call_id); }
  assert.equal((await b.next()).code, 'limit_exceeded');
});

test('injected tools never execute; validation and backend errors are fixed before model reentry', async t => {
  const seen = [];
  const { start } = await setup(t, { streamFn: fake(context => context.messages.some(m => m.role === 'toolResult') ? [] :
    ['bash', 'read', 'write'].map(name => call(name, { path: '/tmp/nere90-forbidden', secret: 'sensitive-argument' }, name))
      .concat([call('submit_draft', { secret: 'sensitive-argument' }, 'invalid'), call('submit_draft')]),
    context => seen.push(context)) });
  const run = await start(); const c = await run.next();
  assert.equal(c.name, 'submit_draft');
  await run.result(c.call_id, 'sensitive-backend-exception', true);
  assert.equal((await run.next()).type, 'done');
  const results = seen.at(-1).messages.filter(m => m.role === 'toolResult');
  assert.equal(results.length, 5);
  assert.ok(results.every(m => m.isError && m.content[0].text === '平台工具调用失败，请检查参数后重试。'));
  assert.equal(existsSync('/tmp/nere90-forbidden'), false);
});

test('quota time uses Retry-After, reset header, estimated minutes, then 30 minutes', () => {
  const now = Date.parse('2026-11-04T00:00:00Z');
  for (const [headers, message, seconds, estimated] of [
    [{ 'retry-after': '60', 'x-ratelimit-reset': '9999' }, '', 60, false],
    [{ 'retry-after': 'Wed, 04 Nov 2026 00:01:00 GMT' }, '', 60, false],
    [{ 'x-ratelimit-reset-requests': '2m30s' }, '', 150, false],
    [{}, 'Please try again in 7 min', 420, true], [{}, 'unknown', 1800, true],
  ]) assert.deepEqual(quota(headers, message, now), { retry_at: new Date(now + seconds * 1000).toISOString(), estimated });
});

test('real SDK error responses retain quota headers without mixing concurrent runs', async t => {
  const upstream = createServer((req, res) => {
    res.writeHead(429, { 'Content-Type': 'application/json', 'Retry-After': req.headers.authorization.endsWith('one') ? '60' : '120' });
    res.end('{"error":{"message":"sensitive error body"}}');
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  t.after(async () => { upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve)); });
  const { request } = await setup(t, { streamFn: undefined });
  const now = Date.now();
  const events = await Promise.all(['one', 'two'].map(async api_key => {
    const data = body(); data.model.baseUrl = `http://127.0.0.1:${upstream.address().port}/v1`; data.model.api_key = api_key;
    const response = await request('/v1/turns', 'POST', data);
    return JSON.parse((await response.text()).trim());
  }));
  for (const [i, event] of events.entries()) {
    assert.equal(event.code, 'rate_limited'); assert.equal(event.estimated, false);
    assert.ok(Math.abs(Date.parse(event.retry_at) - now - (i + 1) * 60000) < 3000);
    assert.equal(JSON.stringify(event).includes('sensitive'), false);
  }
});

test('upstream failures and logs contain no body, authorization, API key or raw errors', async t => {
  let captured = '';
  const streamFn = (model, context, options) => {
    options.onResponse({ status: 429, headers: { 'retry-after': '60', Authorization: 'sensitive-header' } });
    const stream = createAssistantMessageEventStream();
    const message = { role: 'assistant', content: [], stopReason: 'error', errorMessage: 'sensitive-upstream-error' };
    stream.push({ type: 'error', reason: 'error', error: message }); stream.end(); return stream;
  };
  const child = spawn(process.execPath, ['--input-type=module', '-e', `
    import { createPiServer } from './server.mjs';
    import { createAssistantMessageEventStream } from '@mariozechner/pi-ai';
    const server = createPiServer({ token: process.env.PI_SERVICE_TOKEN, streamFn: ${streamFn.toString()} });
    server.listen(0, '127.0.0.1', () => process.send(server.address().port));
    process.on('message', () => server.close(() => process.disconnect()));
  `], { cwd: new URL('.', import.meta.url), env: { ...process.env, PI_SERVICE_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  child.stdout.on('data', chunk => { captured += chunk; });
  child.stderr.on('data', chunk => { captured += chunk; });
  const [port] = await once(child, 'message');
  const response = await fetch(`http://127.0.0.1:${port}/v1/turns`, { method: 'POST', headers: auth, body: JSON.stringify(body()) });
  const event = JSON.parse((await response.text()).trim());
  child.send('stop');
  await once(child, 'exit');
  assert.equal(event.code, 'rate_limited'); assert.equal(event.estimated, false);
  assert.equal(JSON.stringify(event).includes('sensitive'), false);
  assert.match(captured, /"route":"turn"/);
  for (const secret of [token, 'synthetic-api-key', 'synthetic-private-evidence', 'synthetic-system-prompt', 'sensitive', 'Authorization'])
    assert.equal(captured.includes(secret), false, secret);
});
