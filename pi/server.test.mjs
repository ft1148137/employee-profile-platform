import { Agent as HttpAgent } from 'undici';
const controlDispatcher = new HttpAgent();
const fetch = (url, options = {}) => globalThis.fetch(url, { ...options, dispatcher: controlDispatcher });
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { createInterface } from 'node:readline';
import { test } from 'node:test';
import { zstdDecompressSync } from 'node:zlib';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { createPiServer, quota } from './server.mjs';
import { egress } from './egress.mjs';

const jwt = seconds => `synthetic.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + seconds,
  'https://api.openai.com/auth': { chatgpt_account_id: 'synthetic-account' } })).toString('base64url')}.synthetic`;

test('refresh is internal, accepts only refresh, rotates once and redacts upstream failures', async t => {
  let calls = 0;
  const { request, logs } = await setup(t, { refresh: async value => {
    calls++;
    assert.equal(egress.getStore().origin, 'https://auth.openai.com');
    if (value === 'rejected-refresh') throw new Error('secret-access rejected-refresh upstream');
    return { access: 'rotated-access', refresh: 'rotated-refresh', expires: Date.now() + 7200000, accountId: 'synthetic-account' };
  } });
  const path = '/v1/auth/openai-codex/refresh';
  assert.equal((await request(path, 'POST', { refresh: 'secret-refresh' }, {})).status, 401);
  for (const data of [{}, { refresh: '' }, { refresh: 'secret-refresh', org_id: 'x' }])
    assert.equal((await request(path, 'POST', data)).status, 400);
  const rotated = await (await request(path, 'POST', { refresh: 'secret-refresh' })).json();
  assert.equal(rotated.refresh, 'rotated-refresh');
  const failed = await (await request(path, 'POST', { refresh: 'rejected-refresh' })).text();
  assert.equal(JSON.parse(failed).code, 'refresh_failed');
  assert.equal(calls, 2);
  assert.doesNotMatch(failed + JSON.stringify(logs), /secret-|rejected-refresh|rotated-|upstream/);
});

test('locked SDK refresh sends a single request, including when its response is lost', async t => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  let calls = 0;
  globalThis.fetch = async (url, options) => {
    if (new URL(url).origin !== 'https://auth.openai.com') return original(url, options);
    calls++;
    assert.equal(new URL(url).origin, 'https://auth.openai.com');
    assert.equal(options.body.get('refresh_token'), 'secret-refresh');
    throw new Error('secret-refresh upstream');
  };
  const { request, logs } = await setup(t);
  const failed = await (await request('/v1/auth/openai-codex/refresh', 'POST', { refresh: 'secret-refresh' })).text();
  assert.equal(JSON.parse(failed).code, 'refresh_failed');
  assert.equal(calls, 1);
  assert.doesNotMatch(failed + JSON.stringify(logs), /secret-refresh|upstream/);
});

test('locked SDK browser login and refresh keep the HTTP token shape and cancellation', async t => {
  const original = globalThis.fetch;
  const access = jwt(7200);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (String(url).startsWith('http://127.0.0.1:')) return original(url, options);
    assert.equal(String(url), 'https://auth.openai.com/oauth/token');
    calls.push(options.body.get('grant_type'));
    return Response.json({ access_token: access, refresh_token: 'synthetic-refresh', expires_in: 7200 });
  });
  const { request, logs } = await setup(t);
  const path = '/v1/auth/openai-codex/logins';
  const start = await (await request(path, 'POST', {})).json();
  const state = new URL(start.url).searchParams.get('state');
  assert.ok(state);
  const result = await (await request(path + '/' + start.login_id, 'POST',
    { input: `http://localhost:1455/auth/callback?code=synthetic-code&state=${state}` })).json();
  assert.deepEqual(Object.keys(result).sort(), ['access', 'accountId', 'expires', 'refresh']);
  assert.equal(result.access, access);
  assert.equal(result.accountId, 'synthetic-account');
  assert.equal(result.refresh, 'synthetic-refresh');
  const rotated = await (await request('/v1/auth/openai-codex/refresh', 'POST', { refresh: result.refresh })).json();
  assert.deepEqual(Object.keys(rotated).sort(), Object.keys(result).sort());
  assert.equal(rotated.access, access);
  assert.deepEqual(calls, ['authorization_code', 'refresh_token']);
  const pending = await (await request(path, 'POST', {})).json();
  assert.equal((await request(path + '/' + pending.login_id, 'DELETE')).status, 200);
  assert.deepEqual(calls, ['authorization_code', 'refresh_token']);
  assert.ok(!JSON.stringify(logs).includes(access));
  assert.doesNotMatch(JSON.stringify(logs), /synthetic-refresh|synthetic-code/);
});

test('local auth uses only access in a private temporary file, removes it after done/cancel, never refreshes', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'pi-local-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const source = join(directory, 'auth.json');
  const access = jwt(7200);
  const original = JSON.stringify({ tokens: { access_token: access, refresh_token: 'local-refresh', account_id: 'synthetic-account' } });
  await writeFile(source, original, { mode: 0o400 });
  let calls = 0;
  const { start, logs } = await setup(t, { environment: 'development', localAuthFile: source, localAuthDirectory: directory,
    refresh: () => { throw new Error('must never refresh'); },
    streamFn: fake(undefined, (_context, options) => {
      calls++;
      assert.equal(options.apiKey, access);
      const files = readdirSync(directory).filter(n => n.startsWith('codex-'));
      assert.equal(files.length, 1);
      const path = join(directory, files[0], 'access.json');
      assert.equal(statSync(path).mode & 0o777, 0o600);
      assert.deepEqual(JSON.parse(readFileSync(path, 'utf8')), {
        access, accountId: 'synthetic-account', expires: JSON.parse(Buffer.from(access.split('.')[1], 'base64url')).exp * 1000,
      });
    }) });
  for (const cancel of [false, true]) {
    const data = body();
    data.model = { provider: 'openai-codex', api: 'openai-codex-responses', id: 'gpt-5.4',
      baseUrl: 'https://chatgpt.com/backend-api', api_key: 'local-codex', thinkingLevel: 'high' };
    const run = await start(data);
    const event = await run.next();
    if (cancel) await run.cancel();
    else await run.result(event.call_id);
    assert.equal((await run.next()).type, cancel ? 'error' : 'done');
    await run.finished();
    assert.deepEqual(readdirSync(directory), ['auth.json']);
  }
  assert.ok(calls >= 2);
  assert.equal(readFileSync(source, 'utf8'), original);
  assert.doesNotMatch(JSON.stringify(logs), new RegExp(`${access}|local-refresh`));
});

test('local auth requires development and at least an hour remaining', async t => {
  assert.throws(() => createPiServer({ token, localAuthFile: '/unused' }), /development/);
  const directory = await mkdtemp(join(tmpdir(), 'pi-expiry-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const source = join(directory, 'auth.json');
  const { request } = await setup(t, { environment: 'development', localAuthFile: source, localAuthDirectory: directory,
    refresh: () => { throw new Error('must never refresh'); } });
  const data = body();
  data.model = { provider: 'openai-codex', api: 'openai-codex-responses', id: 'gpt-5.4',
    baseUrl: 'https://chatgpt.com/backend-api', api_key: 'local-codex', thinkingLevel: 'high' };
  for (const tokens of [{ access_token: jwt(3599), refresh_token: 'local-refresh' }, { access_token: 'invalid' }]) {
    await writeFile(source, JSON.stringify({ tokens }));
    assert.equal((await (await request('/v1/turns', 'POST', data)).json()).code, 'local_auth_unavailable');
    assert.deepEqual(readdirSync(directory), ['auth.json']);
  }
});

const token = 'synthetic-service-secret';
const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
const call = (name = 'submit_draft', args = { statement: '合成建议' }, id = 'call-1') =>
  ({ type: 'toolCall', id, name, arguments: args });
const body = () => ({ version: 1, run_id: randomUUID(), mode: 'draft',
  model: { provider: 'openai-compatible', api: 'openai-completions', id: 'test', baseUrl: 'http://127.0.0.1:1/v1', api_key: 'synthetic-api-key' },
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
      finished: () => lines.next(),
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
  for (const patch of [{ mode: 'unsupported' }, { callback_url: 'http://db' }, { version: 2 },
    { run_id: 'not-a-uuid' }, { model: { ...body().model, api: 'openai-codex-responses' } },
    { model: { ...body().model, baseUrl: 'file:///etc/passwd' } }])
    assert.equal((await request('/v1/turns', 'POST', { ...body(), ...patch })).status, 400);
});

test('tool results bind to the waiting call and run; duplicate IDs/results do not disturb it', async t => {
  const { request, start } = await setup(t, { streamFn: fake(context => context.messages.some(m => m.role === 'toolResult')
    ? [] : [call('submit_draft', { statement: 'ok' }, context.messages.find(m => m.role === 'user').content[0].text)]) });
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

test('injected tools never execute; rejected arguments are fixed before model reentry', async t => {
  const seen = [];
  const { start } = await setup(t, { streamFn: fake(context => context.messages.some(m => m.role === 'toolResult') ? [] :
    ['bash', 'read', 'write'].map(name => call(name, { path: '/tmp/nere90-forbidden', secret: 'sensitive-argument' }, name))
      .concat([call('submit_draft', { secret: 'sensitive-argument' }, 'invalid'), call('submit_draft')]),
    context => seen.push(context)) });
  const run = await start(); let c = await run.next();
  assert.equal(c.name, 'submit_draft');
  await run.result(c.call_id, { accepted: false, error: '提交不符合要求' });
  c = await run.next(); assert.equal(c.name, 'submit_draft');
  await run.result(c.call_id, { accepted: false });
  assert.equal((await run.next()).type, 'done');
  const results = seen.at(-1).messages.filter(m => m.role === 'toolResult');
  assert.equal(results.length, 5);
  assert.ok(results.slice(0, 4).every(m => m.isError && m.content[0].text === '平台工具调用失败，请检查参数后重试。'));
  assert.deepEqual(JSON.parse(results[4].content[0].text), { accepted: false, error: '提交不符合要求' });
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
    res.writeHead(429, { 'Content-Type': 'application/json', ...(req.headers.authorization.endsWith('none')
      ? {} : { 'Retry-After': req.headers.authorization.endsWith('one') ? '60' : '120' }) });
    res.end('{"error":{"message":"sensitive error body"}}');
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  t.after(async () => { upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve)); });
  const { request } = await setup(t, { streamFn: undefined });
  const now = Date.now();
  const events = await Promise.all(['one', 'two', 'none'].map(async api_key => {
    const data = body(); data.model.baseUrl = `http://127.0.0.1:${upstream.address().port}/v1`; data.model.api_key = api_key;
    const response = await request('/v1/turns', 'POST', data);
    return JSON.parse((await response.text()).trim());
  }));
  for (const [i, event] of events.entries()) {
    assert.equal(event.code, 'rate_limited'); assert.equal(event.estimated, i === 2);
    assert.ok(Math.abs(Date.parse(event.retry_at) - now - [60, 120, 1800][i] * 1000) < 3000);
    assert.equal(JSON.stringify(event).includes('sensitive'), false);
  }
});

test('real SDK classifies upstream errors by status and structured type, with no body in output or logs', async t => {
  const cases = [
    [404, {}, 'model_not_found'], [401, {}, 'model_unauthorized'],
    [400, { type: 'model_not_available' }, 'model_not_found'],
    [403, { code: 'model_not_found' }, 'model_not_found'],
    [403, { type: 'permission_error' }, 'upstream_rejected'],
    [400, { type: 'invalid_request_error' }, 'upstream_rejected'],
    [422, {}, 'upstream_rejected'], [500, {}, 'upstream_unavailable'],
    [503, {}, 'upstream_unavailable'], [429, {}, 'rate_limited'],
    [403, { type: 'usage_limit_reached' }, 'rate_limited'],
    [400, { code: 'insufficient_quota' }, 'rate_limited'],
    [400, null, 'upstream_rejected'],
    [403, { type: 'model_not_found' + 'x'.repeat(17000) }, 'upstream_rejected'],
  ];
  let calls = 0;
  const upstream = createServer((req, res) => {
    calls++;
    const [status, error] = cases[Number(req.headers.authorization.split(' ').at(-1))];
    res.writeHead(status, { 'Content-Type': 'application/json', 'Retry-After': '60' });
    res.end(error === null ? 'synthetic-secret invalid JSON' :
      JSON.stringify({ error: { ...error, message: 'synthetic-secret model_not_found rate_limit' } }));
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  t.after(() => new Promise(resolve => { upstream.closeAllConnections(); upstream.close(resolve); }));
  const { request, logs } = await setup(t, { streamFn: undefined });
  for (let i = 0; i < cases.length; i++) {
    const data = body();
    data.model.baseUrl = `http://127.0.0.1:${upstream.address().port}/v1`;
    data.model.api_key = String(i);
    const output = await (await request('/v1/turns', 'POST', data)).text();
    assert.equal(JSON.parse(output.trim()).code, cases[i][2], `case ${i}`);
    assert.doesNotMatch(output + JSON.stringify(logs), /synthetic-secret|invalid_request_error|permission_error/);
  }
  assert.equal(calls, cases.length);
});

test('request fetch captures structured errors for Responses, Anthropic and Codex too', async t => {
  const original = globalThis.fetch;
  t.mock.method(globalThis, 'fetch', (url, options) => String(url).startsWith('http://127.0.0.1:')
    ? original(url, options)
    : Promise.resolve(Response.json({ error: { code: 'model_not_found', message: 'synthetic-secret' } }, { status: 403 })));
  const { request, logs } = await setup(t, { streamFn: undefined });
  for (const [provider, api, id, baseUrl, api_key] of [
    ['openai', 'openai-responses', 'gpt-4o-mini', 'https://api.openai.com/v1', 'synthetic-key'],
    ['anthropic', 'anthropic-messages', 'claude-sonnet-4-5', 'https://api.anthropic.com', 'synthetic-key'],
    ['openai-codex', 'openai-codex-responses', 'gpt-5.4', 'https://chatgpt.com/backend-api', jwt(7200)],
  ]) {
    const data = { ...body(), model: { provider, api, id, baseUrl, api_key } };
    const output = await (await request('/v1/turns', 'POST', data)).text();
    assert.deepEqual(JSON.parse(output.trim()), { type: 'error', code: 'model_not_found' }, provider);
    assert.doesNotMatch(output + JSON.stringify(logs), /synthetic-secret|synthetic-key/);
  }
});

test('connection failures are unavailable, unclassified SDK errors remain model_error', async t => {
  const { request, logs } = await setup(t, { streamFn: undefined });
  const output = await (await request('/v1/turns', 'POST', body())).text();
  assert.equal(JSON.parse(output.trim()).code, 'upstream_unavailable');
  assert.doesNotMatch(output + JSON.stringify(logs), /synthetic-api-key|ECONNREFUSED/);
  const unknown = await setup(t, { streamFn: () => { throw new Error('synthetic-secret model_not_found'); } });
  const unclassified = await (await unknown.request('/v1/turns', 'POST', body())).text();
  assert.deepEqual(JSON.parse(unclassified.trim()), { type: 'error', code: 'model_error' });
  assert.doesNotMatch(unclassified + JSON.stringify(unknown.logs), /synthetic-secret/);
});

test('deadline interrupts real SDK network waiting, not only platform tools', async t => {
  let closed;
  const disconnected = new Promise(resolve => { closed = resolve; });
  const upstream = createServer((_req, res) => res.on('close', closed));
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  t.after(async () => { upstream.closeAllConnections(); await new Promise(resolve => upstream.close(resolve)); });
  const { request, logs } = await setup(t, { streamFn: undefined, deadlineMs: 500 });
  for (const data of [body(), chat()]) {
    data.model.baseUrl = `http://127.0.0.1:${upstream.address().port}/v1`;
    const response = await request('/v1/turns', 'POST', data);
    assert.deepEqual(JSON.parse((await response.text()).trim()), { type: 'error', code: 'deadline' });
  }
  const failures = logs.filter(entry => entry.route === 'turn');
  assert.equal(failures.length, 2);
  assert.ok(failures.every(entry => entry.code === 'deadline' && entry.elapsed_ms >= 500));
  await disconnected;
  assert.equal((await request('/health')).status, 200);
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
    import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
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


test('catalog capabilities, model identity, API and reasoning cannot be overridden', async t => {
  const { request } = await setup(t);
  assert.equal((await request('/v1/models/openai/gpt-4o-mini', 'GET', undefined, {})).status, 401);
  assert.deepEqual(await (await request('/v1/models/openai/gpt-4o-mini')).json(),
    { in_catalog: true, api: 'openai-responses', thinking_levels: ['off'] });
  assert.equal((await request('/v1/models/openai/not-in-catalog')).status, 400);
  assert.equal((await request('/v1/models/unknown/model')).status, 400);
  assert.deepEqual(await (await request('/v1/models/openai-codex/gpt-6.1-sol')).json(),
    { in_catalog: true, api: 'openai-codex-responses', thinking_levels: ['minimal', 'low', 'medium', 'high', 'xhigh'] });
  const supported = await (await request('/v1/models/openai-compatible/local%2Fmodel')).json();
  assert.equal(supported.in_catalog, false);
  assert.ok(supported.thinking_levels.includes('xhigh'));
  for (const patch of [{ api: 'openai-completions' }, { reasoning: false }, { maxTokens: 1 },
    { contextWindow: 1 }, { compat: {} }, { thinkingLevel: 'high' }]) {
    const data = body();
    data.model = { ...data.model, provider: 'openai', id: 'gpt-4o-mini', api: 'openai-responses', ...patch };
    assert.equal((await request('/v1/turns', 'POST', data)).status, 400);
  }
});

test('uncataloged Codex models inherit transport and conservative reasoning, with validated IDs', async t => {
  const { request } = await setup(t);
  const model = { provider: 'openai-codex', api: 'openai-codex-responses', id: 'uncataloged-model',
    baseUrl: 'https://chatgpt.com/backend-api', thinkingLevel: 'high' };
  assert.deepEqual(await (await request('/v1/models/openai-codex/uncataloged-model')).json(),
    { in_catalog: false, api: 'openai-codex-responses', thinking_levels: ['off', 'minimal', 'low', 'medium', 'high'] });
  for (const id of ['a'.repeat(100), 'Model_1.2-test'])
    assert.equal((await request('/v1/models/openai-codex/' + id)).status, 200);
  for (const id of ['a'.repeat(101), 'bad/model', 'bad?model', 'bad model', '模型']) {
    assert.deepEqual(await (await request('/v1/models/openai-codex/' + encodeURIComponent(id))).json(),
      { type: 'error', code: 'bad_request' });
    assert.deepEqual(await (await request('/v1/turns', 'POST', { ...body(), model: { ...model, id } })).json(),
      { type: 'error', code: 'bad_request' });
  }
  for (const patch of [{ id: '' }, { thinkingLevel: 'xhigh' }, { reasoning: false },
    { baseUrl: 'https://example.com/backend-api' }, { api: 'openai-responses' }])
    assert.equal((await request('/v1/turns', 'POST', { ...body(), model: { ...model, ...patch } })).status, 400);
});

test('egress rejects redirects without contacting their target', async t => {
  let requests = 0;
  const upstream = createServer((_req, res) => {
    requests++;
    res.writeHead(302, { Location: '/target' }); res.end();
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  t.after(() => new Promise(resolve => { upstream.closeAllConnections(); upstream.close(resolve); }));
  const { request } = await setup(t, { streamFn: undefined });
  const data = body(); data.model.baseUrl = `http://127.0.0.1:${upstream.address().port}/v1`;
  const response = await request('/v1/turns', 'POST', data);
  assert.equal(JSON.parse((await response.text()).trim()).code, 'upstream_unavailable');
  assert.equal(requests, 1);
});

test('DNS results, literals and origin policies are checked at each connection', async t => {
  const { egress, checkAddress } = await import('./egress.mjs');
  for (const ip of ['169.254.169.254', '169.254.1.1', 'fe80::1', 'fd00:ec2::254', '::ffff:169.254.169.254'])
    assert.throws(() => checkAddress(ip, { catalog: false }));
  for (const ip of ['127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '::1', 'fd00::1', 'fec0::1', '::ffff:127.0.0.1'])
    assert.throws(() => checkAddress(ip, { catalog: true }));
  const { request } = await setup(t, { streamFn: undefined,
    lookup: (_host, _options, callback) => callback(null, [{ address: '192.168.1.1', family: 4 }]) });
  const data = body(); data.model = { ...data.model, provider: 'openai', api: 'openai-responses', id: 'gpt-4o-mini', baseUrl: 'http://synthetic.invalid/v1' };
  const response = await request('/v1/turns', 'POST', data);
  assert.equal(JSON.parse((await response.text()).trim()).code, 'upstream_unavailable');
  await assert.rejects(egress.run({ catalog: false, origin: 'http://allowed.invalid:123' },
    () => globalThis.fetch('http://other.invalid:123')));
  await assert.rejects(egress.run({ catalog: false, origin: 'http://allowed.invalid:123' },
    () => globalThis.fetch('http://allowed.invalid:124')));
  await assert.rejects(globalThis.fetch('http://no-policy.invalid'));
});


test('development fake IP opt-in permits only the proxy range', async t => {
  const { checkAddress } = await import('./egress.mjs');
  const previous = process.env.PI_EGRESS_ALLOW_FAKE_IP;
  try {
    process.env.PI_EGRESS_ALLOW_FAKE_IP = '1';
    await setup(t, { environment: 'development' });
    for (const ip of ['198.18.0.1', '198.19.255.254', '::ffff:198.18.0.36'])
      assert.doesNotThrow(() => checkAddress(ip, { catalog: true }));
    for (const ip of ['10.0.0.1', '127.0.0.1', '172.16.0.1', '192.168.0.1',
      '169.254.169.254', 'fd00::1', 'fe80::1'])
      assert.throws(() => checkAddress(ip, { catalog: true }));
  } finally {
    if (previous === undefined) delete process.env.PI_EGRESS_ALLOW_FAKE_IP;
    else process.env.PI_EGRESS_ALLOW_FAKE_IP = previous;
  }
});

test('fake IP destinations remain blocked without opt-in', async () => {
  const { checkAddress } = await import('./egress.mjs');
  const previous = process.env.PI_EGRESS_ALLOW_FAKE_IP;
  try {
    delete process.env.PI_EGRESS_ALLOW_FAKE_IP;
    for (const ip of ['198.18.0.36', '198.19.1.1', '::ffff:198.18.0.36'])
      assert.throws(() => checkAddress(ip, { catalog: true }));
  } finally {
    if (previous !== undefined) process.env.PI_EGRESS_ALLOW_FAKE_IP = previous;
  }
});

test('fake IP opt-in prevents production or unspecified startup', () => {
  const previous = process.env.PI_EGRESS_ALLOW_FAKE_IP;
  try {
    process.env.PI_EGRESS_ALLOW_FAKE_IP = '1';
    for (const environment of [undefined, 'production'])
      assert.throws(() => createPiServer({ token, environment }), /requires development/);
  } finally {
    if (previous === undefined) delete process.env.PI_EGRESS_ALLOW_FAKE_IP;
    else process.env.PI_EGRESS_ALLOW_FAKE_IP = previous;
  }
});

test('a second connection rechecks DNS instead of reusing an allowed socket', async t => {
  let resolutions = 0; let received = 0;
  const upstream = createServer((_req, res) => { received++; res.writeHead(429, { 'Retry-After': '60' }); res.end('{}'); });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  t.after(() => new Promise(resolve => { upstream.closeAllConnections(); upstream.close(resolve); }));
  const { request } = await setup(t, { streamFn: undefined,
    lookup: (_host, _opts, callback) => callback(null, [{ address: ++resolutions === 1 ? '127.0.0.1' : '169.254.169.254', family: 4 }]) });
  for (const code of ['rate_limited', 'upstream_unavailable']) {
    const data = body(); data.model.baseUrl = `http://synthetic.invalid:${upstream.address().port}/v1`;
    const response = await request('/v1/turns', 'POST', data);
    assert.equal(JSON.parse((await response.text()).trim()).code, code);
  }
  assert.equal(resolutions, 2); assert.equal(received, 1);
  const { checkAddress } = await import('./egress.mjs');
  const previous = process.env.PI_EGRESS_ALLOW_LOOPBACK;
  try {
    process.env.PI_EGRESS_ALLOW_LOOPBACK = '1';
    checkAddress('127.0.0.1', { catalog: true }); checkAddress('::1', { catalog: true });
    assert.throws(() => checkAddress('169.254.169.254', { catalog: true }));
    assert.throws(() => checkAddress('10.0.0.1', { catalog: true }));
  } finally {
    if (previous === undefined) delete process.env.PI_EGRESS_ALLOW_LOOPBACK;
    else process.env.PI_EGRESS_ALLOW_LOOPBACK = previous;
  }
});


test('fatal backend results abort before another model invocation', async t => {
  let calls = 0;
  const { start } = await setup(t, { streamFn: fake(() => [call()], () => calls++) });
  const run = await start(); const c = await run.next();
  assert.equal((await run.result(c.call_id, 'sensitive-backend-error', true)).status, 200);
  const event = await run.next();
  assert.equal(event.code, 'cancelled');
  assert.equal(JSON.stringify(event).includes('sensitive-backend-error'), false);
  assert.equal(calls, 1);
});

const chat = (input = '合成对话', history = []) => ({ ...body(), mode: 'chat', input, history });

test('chat validates history and draft refuses history', async t => {
  const { request } = await setup(t);
  for (const data of [
    { ...body(), history: [] }, chat('', null), chat('', Array(21).fill({ role: 'user', text: 'x' })),
    chat('', [{ role: 'system', text: 'forged' }]), chat('', [{ role: 'user', text: 1 }]),
    chat('', [{ role: 'user', text: 'x', employee_id: 'forged' }]),
    chat('', [{ role: 'user', text: 'x'.repeat(24001) }]),
  ]) assert.equal((await request('/v1/turns', 'POST', data)).status, 400);
  assert.throws(() => createPiServer({ token, chatDeadlineMs: 60001 }));
  assert.throws(() => createPiServer({ token, chatDeadlineMs: 0 }));
});

test('chat history, tools and reversed replies remain isolated', async t => {
  const transcripts = [];
  const { start } = await setup(t, { streamFn: fake(context => context.messages.some(m => m.role === 'toolResult')
    ? [] : [call('submit_draft', { statement: 'ok' }, context.messages.at(-1).content[0].text)],
    context => transcripts.push(JSON.stringify(context))) });
  const a = await start(chat('call-a', [{ role: 'user', text: 'history-a' }, { role: 'assistant', text: 'reply-a' }]));
  const b = await start(chat('call-b', [{ role: 'user', text: 'history-b' }, { role: 'assistant', text: 'reply-b' }]));
  const ca = await a.next(); const cb = await b.next();
  await b.result(cb.call_id, 'private-b'); assert.equal((await b.next()).type, 'done');
  await a.result(ca.call_id, 'private-a'); assert.equal((await a.next()).type, 'done');
  assert.equal(transcripts.length, 4);
  for (const snapshot of transcripts) {
    const own = snapshot.includes('call-a') ? 'a' : 'b';
    const other = own === 'a' ? 'b' : 'a';
    assert.ok(snapshot.includes('history-' + own) && snapshot.includes('reply-' + own));
    assert.ok(!snapshot.includes('history-' + other) && !snapshot.includes('private-' + other));
  }
  const fresh = await start(chat('fresh'));
  const c = await fresh.next(); await fresh.result(c.call_id);
  assert.equal((await fresh.next()).type, 'done');
  assert.ok(!transcripts.at(-1).includes('history-'));
});

test('chat rejects programming tools just like draft', async t => {
  const seen = [];
  const { start } = await setup(t, { streamFn: fake(context => context.messages.some(m => m.role === 'toolResult')
    ? [] : ['bash', 'read', 'write', 'exec', 'edit'].map(name => call(name, { path: '/tmp/nere97-forbidden' }, name)),
    context => seen.push(context)) });
  const run = await start(chat());
  assert.equal((await run.next()).type, 'done');
  const results = seen.at(-1).messages.filter(m => m.role === 'toolResult');
  assert.equal(results.length, 5);
  assert.ok(results.every(m => m.isError));
  assert.equal(existsSync('/tmp/nere97-forbidden'), false);
});

test('chat deadline includes backend tools and releases capacity', async t => {
  const { start } = await setup(t, { chatDeadlineMs: 120 });
  const run = await start(chat()); const c = await run.next();
  assert.equal((await run.next()).code, 'deadline');
  assert.equal((await run.result(c.call_id)).status, 409);
  const fresh = await start(chat()); const next = await fresh.next();
  await fresh.result(next.call_id); assert.equal((await fresh.next()).type, 'done');
});

test('delayed chat load preserves one draft slot without queueing', async t => {
  const { request, start } = await setup(t);
  const started = performance.now();
  let accepted = 0; let rejected = 0;
  for (let batch = 0; batch < 8; batch++) {
    const runs = await Promise.all(Array.from({ length: 3 }, () => start(chat())));
    await Promise.all(runs.map(run => run.next()));
    const busy = await request('/v1/turns', 'POST', chat());
    assert.equal(busy.status, 503); assert.equal((await busy.json()).code, 'busy'); rejected++;
    const draft = await start(); const cd = await draft.next(); accepted += 4;
    const full = await request('/v1/turns', 'POST', body());
    assert.equal(full.status, 503); rejected++;
    await new Promise(resolve => setTimeout(resolve, 30));
    // Finish in reverse order to exercise pending result ownership under load.
    await draft.result(cd.call_id); await draft.next();
    for (const run of runs.reverse()) { await run.result('call-1'); assert.equal((await run.next()).type, 'done'); }
  }
  t.diagnostic(JSON.stringify({ batches: 8, tool_delay_ms: 30, accepted, rejected,
    elapsed_ms: Math.round(performance.now() - started), process_limit: 4, chat_limit: 3 }));
});

test('OAuth helpers authenticate, serialize logins, cancel, expire and redact failures', async t => {
  let exchange; const exchanging = new Promise(resolve => { exchange = resolve; });
  const login = async ({ onAuth, onManualCodeInput }) => {
    onAuth({ url: 'https://auth.openai.com/oauth/authorize?state=synthetic-state' });
    const input = await onManualCodeInput();
    if (input === 'failure') throw new Error('synthetic-refresh private upstream');
    if (input === 'slow') return new Promise((_, reject) => {
      egress.getStore().signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true });
      exchange();
    });
    return { access: 'synthetic-access', refresh: 'synthetic-refresh', expires: Date.now() + 7200000, accountId: 'synthetic-account' };
  };
  const { request, logs } = await setup(t, { login, loginLifetime: 1000 });
  const path = '/v1/auth/openai-codex/logins';
  for (const [route, method] of [[path, 'POST'], [path + '/unknown', 'POST'], [path + '/unknown', 'DELETE']])
    assert.equal((await request(route, method, {}, {})).status, 401);
  const begin = async () => (await request(path, 'POST', {})).json();
  let start = await begin();
  assert.match(start.url, /state=synthetic-state/);
  assert.equal((await begin()).code, 'login_busy');
  let result = await request(path + '/' + start.login_id, 'POST', { input: 'redirect-url' });
  assert.equal((await result.json()).refresh, 'synthetic-refresh');
  assert.equal((await (await request(path + '/' + start.login_id, 'POST', { input: 'replay' })).json()).code, 'login_not_found');
  start = await begin();
  assert.equal((await request(path + '/' + start.login_id, 'DELETE')).status, 200);
  start = await begin();
  result = await request(path + '/' + start.login_id, 'POST', { input: 'failure' });
  assert.deepEqual(await result.json(), { code: 'login_failed' });
  start = await begin();
  const pending = request(path + '/' + start.login_id, 'POST', { input: 'slow' });
  await exchanging;
  assert.equal((await request(path + '/' + start.login_id, 'DELETE')).status, 200);
  assert.deepEqual(await (await pending).json(), { code: 'login_failed' });
  start = await begin();
  await new Promise(resolve => setTimeout(resolve, 1100));
  assert.equal((await (await request(path + '/' + start.login_id, 'POST', { input: 'late' })).json()).code, 'login_not_found');
  assert.ok((await begin()).login_id);
  assert.ok(!JSON.stringify(logs).includes('synthetic-'));
});

test('Codex turns use only responses with an access token and fixed SSE', async t => {
  const access = `e30.${Buffer.from(JSON.stringify({ 'https://api.openai.com/auth': { chatgpt_account_id: 'synthetic-account' } })).toString('base64url')}.synthetic`;
  const original = globalThis.fetch;
  const seen = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (String(url).startsWith('http://127.0.0.1:')) return original(url, options);
    seen.push(JSON.parse(options.headers.get('content-encoding') === 'zstd'
      ? zstdDecompressSync(options.body).toString() : options.body));
    assert.equal(String(url), 'https://chatgpt.com/backend-api/codex/responses');
    assert.equal(options.headers.get('Authorization'), `Bearer ${access}`);
    return new Response('sensitive synthetic-refresh', { status: 401 });
  });
  const { start, logs } = await setup(t, { streamFn: undefined });
  const data = body();
  data.model = { provider: 'openai-codex', api: 'openai-codex-responses', id: 'gpt-5.4',
    baseUrl: 'https://chatgpt.com/backend-api', api_key: access };
  for (const id of ['gpt-5.4', 'gpt-6.1-sol']) {
    for (const mode of ['draft', 'chat']) {
      const run = await start({ ...data, model: { ...data.model, id, thinkingLevel: 'high' }, mode,
        ...(mode === 'chat' ? { history: [{ role: 'user', text: 'synthetic history' }] } : {}) });
      assert.deepEqual(await run.next(), { type: 'error', code: 'model_unauthorized' });
      assert.equal(seen.at(-1).model, id);
      assert.equal(seen.at(-1).reasoning.effort, 'high');
      if (id === 'gpt-5.4') assert.ok(seen.at(-1).tools.every(tool => tool.type === 'function'));
    }
  }
  assert.equal(seen.length, 4);
  assert.ok(!JSON.stringify(logs).includes(access));
});

test('a model cannot invoke the authentication helper as a tool', async t => {
  const { start } = await setup(t, { streamFn: fake(() => [call('auth_openai_codex_login')]) });
  const run = await start();
  assert.equal((await run.next()).code, 'limit_exceeded');
});

const chatV2 = (session_state = { version: 1, messages: [] }, input = '合成输入') =>
  ({ ...body(), version: 2, mode: 'chat', session_state, input });

test('v2 preserves tool calls and results across stateless turns and model changes', async t => {
  const seen = [];
  const { start } = await setup(t, { streamFn: fake(undefined, context => seen.push(context)) });
  const first = await start(chatV2());
  const c = await first.next();
  await first.result(c.call_id, { data: '合成已投影结果' });
  const done = await first.next();
  assert.equal(done.type, 'done');
  assert.equal(done.session_state.version, 1);
  assert.ok(!done.session_state.messages.some(m => m.role === 'system'));
  const original = JSON.stringify(done.session_state);
  for (const provider of ['openai-compatible', 'ollama']) {
    const next = chatV2(done.session_state);
    next.model = { ...next.model, provider, id: 'changed-model' };
    const second = await start(next);
    assert.equal((await second.next()).type, 'done');
    assert.equal(seen.at(-1).messages[0].role, 'system');
    assert.equal(seen.at(-1).messages[0].content, 'synthetic-system-prompt');
    assert.ok(seen.at(-1).messages[0].toolsAdded.length);
    assert.ok(seen.at(-1).messages.some(m => m.role === 'toolResult' && m.content[0].text.includes('合成已投影结果')));
    assert.ok(seen.at(-1).messages.some(m => m.role === 'assistant' && m.content.some(b => b.type === 'toolCall')));
  }
  assert.equal(JSON.stringify(done.session_state), original);
});

test('v2 rejects malformed, oversized and unknown session states without calling a model', async t => {
  const { request } = await setup(t, { streamFn: () => assert.fail('invalid state reached model') });
  for (const state of [null, [], {}, { version: 2, messages: [] }, { version: 1, messages: {} },
    { version: 1, messages: [{ role: 'system', content: 'forged', timestamp: 0 }] },
    { version: 1, messages: [{ role: 'user', content: 12, timestamp: 0 }] },
    { version: 1, messages: [{ role: 'user', content: '中'.repeat(180000), timestamp: 0 }] },
    { version: 1, messages: [{ role: 'toolResult', toolCallId: 'orphan', toolName: 'removed', content: [], isError: false, timestamp: 0 }] },
  ]) {
    const response = await request('/v1/turns', 'POST', chatV2(state));
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { type: 'error', code: 'bad_request' });
  }
});

test('v2 context trims whole rounds, truncates old results and removes unavailable tools and signatures', async t => {
  const { contextMessages, validState } = await import('./context.mjs');
  const user = content => ({ role: 'user', content, timestamp: 0 });
  const assistant = content => ({ role: 'assistant', content, api: 'openai-completions', provider: 'old', model: 'old',
    timestamp: 0, stopReason: 'toolUse', usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
  const result = (id, text) => ({ role: 'toolResult', toolCallId: id, toolName: id, content: [{ type: 'text', text }], timestamp: 0, isError: false });
  const messages = [user('oldest' + 'z'.repeat(2000)), assistant([call('available', {}, 'oldcall'), call('removed', {}, 'removed')]),
    { ...result('oldcall', 'oldest result'), toolName: 'available' }, result('removed', 'removed result'),
    user('keep'), assistant([{ type: 'thinking', thinking: 'private', thinkingSignature: 'foreign-signature' },
      { type: 'text', text: 'answer', textSignature: 'foreign-text' }, call('available', {}, 'available')]),
    result('available', 'x'.repeat(10000)), user('current')];
  const model = { api: 'openai-completions', provider: 'new', id: 'new', contextWindow: 20000 };
  const tools = [{ name: 'available' }];
  const trimmed = contextMessages(messages, model, tools, 2, 'system');
  assert.equal(trimmed[0].content, 'keep');
  assert.ok(!JSON.stringify(trimmed).includes('oldest'));
  assert.ok(!JSON.stringify(trimmed).includes('oldcall'));
  assert.ok(!JSON.stringify(trimmed).includes('foreign-'));
  assert.equal(trimmed.find(m => m.role === 'toolResult').content[0].text, 'x'.repeat(8000) + '（已截断）');
  assert.ok(validState({ version: 1, messages: trimmed }));
  const removed = contextMessages(messages, { ...model, contextWindow: 100000 }, [], 2, 'system');
  assert.ok(!removed.some(m => m.role === 'toolResult'));
  assert.ok(!removed.some(m => m.role === 'assistant' && m.content.some(b => b.type === 'toolCall')));
  const current = contextMessages(messages, model, tools, 1, 'system');
  assert.ok(!current.some(m => m.role === 'toolResult')); // The oversized earlier round is removed as a whole.
  const same = contextMessages(messages, { ...model, provider: 'old', id: 'old', contextWindow: 100000 }, tools, 2, 'system');
  assert.ok(JSON.stringify(same).includes('foreign-signature'));
  assert.deepEqual(contextMessages([user('x'.repeat(600000))], model, tools, 0, '', true), []);
});


test('v2 schema failures with reused call IDs use only current arguments and are not replayed', async t => {
  const { start } = await setup(t, { streamFn: fake(context => {
    const current = context.messages.slice(context.messages.findLastIndex(m => m.role === 'user'));
    return current[0].content[0].text === 'third' || current.some(m => m.role === 'toolResult') ? [] : [call('submit_draft',
      current[0].content[0].text === 'first' ? { statement: 'old' } : { invalid: true })];
  }) });
  const first = await start(chatV2(undefined, 'first'));
  const c = await first.next();
  await first.result(c.call_id);
  const previous = await first.next();
  const second = await start(chatV2(previous.session_state, 'second'));
  const rejected = await second.next();
  assert.deepEqual(rejected.arguments, { invalid: true });
  await second.result(rejected.call_id, { data: { error: '参数不正确' } });
  const done = await second.next();
  assert.equal(done.type, 'done');
  const thirdData = chatV2(done.session_state, 'third');
  thirdData.tools = [];
  const third = await start(thirdData);
  assert.equal((await third.next()).type, 'done');
});
