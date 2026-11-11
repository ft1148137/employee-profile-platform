import { Agent } from '@earendil-works/pi-agent-core';
import { getModel, getProviders, getSupportedThinkingLevels, streamSimple } from './runtime.mjs';
import { createHash, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { pathToFileURL } from 'node:url';

import { contextMessages, validState } from './context.mjs';
import { egress } from './egress.mjs';
import { authLogins, authRefresh } from './auth.mjs';
import { localCredentials } from './local-auth.mjs';

const customProviders = new Set(['qwen', 'openai-compatible', 'ollama']);
const thinkingLevels = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'];
function catalogModel(provider, id) {
  if (provider === 'openai-codex' && !/^[A-Za-z0-9._-]{1,100}$/.test(id)) throw new Error('bad_request');
  if (!getProviders().includes(provider)) {
    if (!customProviders.has(provider)) throw new Error('bad_request');
    return undefined;
  }
  const model = getModel(provider, id);
  if (!model && provider === 'openai-codex') {
    const template = getModel(provider, 'gpt-6.1-sol');
    return { ...template, id, reasoning: true, compat: undefined, thinkingLevelMap:
      { off: 'none', minimal: 'low', low: 'low', medium: 'medium', high: 'high', xhigh: null, max: null } };
  }
  if (!model) throw new Error('bad_request');
  return model;
}
function levels(model) {
  if (!model) return thinkingLevels;
  if (model.compat?.supportsReasoningEffort === false) return ['off'];
  return getSupportedThinkingLevels(model).filter(level => thinkingLevels.includes(level));
}
function resolveModel(m) {
  const catalog = catalogModel(m.provider, m.id);
  if (catalog) {
    if ((m.provider === 'openai-codex' && m.baseUrl !== catalog.baseUrl) ||
      m.api !== catalog.api || ['reasoning', 'thinkingLevelMap', 'contextWindow', 'maxTokens', 'compat']
      .some(key => Object.hasOwn(m, key)) || !levels(catalog).includes(m.thinkingLevel ?? 'off')) throw new Error('bad_request');
    return { ...catalog, baseUrl: m.baseUrl };
  }
  return { id: m.id, name: m.id, api: m.api, provider: m.provider, baseUrl: m.baseUrl,
    reasoning: m.reasoning ?? false, thinkingLevelMap: { xhigh: 'xhigh' }, input: ['text'], contextWindow: m.contextWindow ?? 128000,
    maxTokens: m.maxTokens ?? 4096, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, compat: m.compat };
}

const TOOL_ERROR = '平台工具调用失败，请检查参数后重试。';
const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.length > 0;
const only = (value, keys) => object(value) && Object.keys(value).every(k => keys.includes(k));
const allowedHeader = name => /^(retry-after|[a-z0-9-]*reset[a-z0-9-]*)$/i.test(name);
const unavailableModels = new Set(['model_not_found', 'model_not_available', 'model_not_supported',
  'unsupported_model', 'invalid_model', 'model_access_denied']);
const quotaTypes = new Set(['insufficient_quota', 'rate_limit_exceeded', 'usage_limit_reached', 'usage_not_included']);

async function errorCode(response) {
  const reader = response.clone().body?.getReader();
  if (!reader) return undefined;
  try {
    let size = 0;
    const chunks = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 16384) return undefined;
      chunks.push(value);
    }
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const types = [body?.error?.type, body?.error?.code, body?.type, body?.code];
    if (types.some(type => unavailableModels.has(type))) return 'model_not_found';
    if (types.some(type => quotaTypes.has(type))) return 'rate_limited';
  } catch { return undefined; }
  finally { void reader.cancel().catch(() => {}); }
}

function upstreamCode(response) {
  if (response.status === 401) return 'model_unauthorized';
  if (response.status === 404 || response.code === 'model_not_found') return 'model_not_found';
  if (response.status >= 500 || response.networkError) return 'upstream_unavailable';
  if (response.status >= 400) return 'upstream_rejected';
  return 'model_error';
}

function validate(body) {
  if (!only(body, ['version', 'run_id', 'mode', 'model', 'system_prompt', 'input', 'tools', 'history', 'session_state']) ||
      !([1, 2].includes(body.version)) || (body.mode === 'draft' && body.version !== 1) || !uuid.test(body.run_id) || !['draft', 'chat'].includes(body.mode) ||
      typeof body.system_prompt !== 'string' || typeof body.input !== 'string' ||
      !Array.isArray(body.tools) || body.tools.length > 16) return false;
  if (body.version === 2 && (body.mode !== 'chat' || Object.hasOwn(body, 'history') || !validState(body.session_state))) return false;
  if (body.version === 1 && Object.hasOwn(body, 'session_state')) return false;
  if (Object.hasOwn(body, 'history') && (body.mode !== 'chat' || !Array.isArray(body.history) ||
      body.history.length > 20 || !body.history.every(m => only(m, ['role', 'text']) &&
        ['user', 'assistant'].includes(m.role) && typeof m.text === 'string') ||
      body.history.reduce((sum, m) => sum + Array.from(m.text).length, 0) > 24000)) return false;
  const m = body.model;
  if (!only(m, ['provider', 'api', 'id', 'baseUrl', 'api_key', 'thinkingLevel',
    'reasoning', 'contextWindow', 'maxTokens', 'compat']) ||
    !['openai-responses', 'anthropic-messages', 'openai-completions', 'openai-codex-responses'].includes(m.api) ||
    (m.api === 'openai-codex-responses') !== (m.provider === 'openai-codex') ||
    !text(m.provider) || !text(m.id) || !text(m.baseUrl) ||
    (m.api_key !== undefined && typeof m.api_key !== 'string') ||
    (m.reasoning !== undefined && typeof m.reasoning !== 'boolean') ||
    (m.compat !== undefined && !object(m.compat)) ||
    !['off', 'minimal', 'low', 'medium', 'high', 'xhigh'].includes(m.thinkingLevel ?? 'off') ||
    ![m.contextWindow ?? 128000, m.maxTokens ?? 4096].every(n => Number.isSafeInteger(n) && n > 0)) return false;
  try {
    const url = new URL(m.baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.hash) return false;
  } catch { return false; }
  const names = new Set();
  return body.tools.every(t => {
    if (!only(t, ['name', 'description', 'parameters']) || !/^[a-zA-Z0-9_-]{1,64}$/.test(t.name) ||
      typeof t.description !== 'string' || !object(t.parameters) || t.parameters.type !== 'object' ||
      names.has(t.name)) return false;
    names.add(t.name);
    return true;
  });
}

async function readBody(req) {
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 1024 * 1024) throw new Error('bad_request');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

export function quota(headers = {}, message = '', now = Date.now()) {
  const retry = headers['retry-after'];
  let at;
  if (retry) at = /^\d+(\.\d+)?$/.test(retry) ? now + Number(retry) * 1000 : Date.parse(retry);
  if (!Number.isFinite(at)) {
    for (const [name, value] of Object.entries(headers)) {
      if (!/reset/i.test(name)) continue;
      if (/^\d+(\.\d+)?$/.test(value)) {
        const n = Number(value);
        at = n > 1e12 ? n : n > 1e9 ? n * 1000 : now + n * 1000;
      } else if (/^(?:\d+(?:\.\d+)?[hms])+$/.test(value)) {
        at = now + [...value.matchAll(/([\d.]+)([hms])/g)]
          .reduce((sum, [, n, unit]) => sum + Number(n) * { h: 3600000, m: 60000, s: 1000 }[unit], 0);
      } else at = Date.parse(value);
      if (Number.isFinite(at)) break;
    }
  }
  let estimated = !Number.isFinite(at);
  if (estimated) {
    const minutes = message.match(/(?:try again in|retry (?:in|after))\s*(\d+(?:\.\d+)?)\s*(?:min|minute)/i);
    at = now + (minutes ? Number(minutes[1]) : 30) * 60000;
  }
  if (!Number.isFinite(at) || Math.abs(at) > 8.64e15) { at = now + 1800000; estimated = true; }
  return { retry_at: new Date(Math.max(now, at)).toISOString(), estimated };
}

export function createPiServer({ token, deadlineMs = 120000, chatDeadlineMs = 60000, streamFn = streamSimple,
  lookup, login, loginLifetime, refresh, localAuthFile, localAuthDirectory = '/run/pi-auth',
  environment, log = entry => console.log(JSON.stringify(entry)) } = {}) {
  if (localAuthFile && environment !== 'development') throw new Error('Local auth requires development');
  if (process.env.PI_EGRESS_ALLOW_FAKE_IP === '1' && environment !== 'development')
    throw new Error('Fake IP egress requires development');
  if (!text(token) || !Number.isFinite(deadlineMs) || deadlineMs <= 0 || deadlineMs > 120000 ||
      !Number.isFinite(chatDeadlineMs) || chatDeadlineMs <= 0 || chatDeadlineMs > 60000)
    throw new Error('Invalid Pi service configuration');
  const digest = value => createHash('sha256').update(value).digest();
  const expected = digest(`Bearer ${token}`);
  const runs = new Map();
  const authenticate = authLogins(login, loginLifetime);
  const json = (res, status, data) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)); };
  const server = createServer({ requestTimeout: 10000, headersTimeout: 10000 }, async (req, res) => {
    const start = Date.now();
    let route = 'unknown';
    let runId;
    let code;
    res.on('close', () => log({ route, status: res.statusCode, elapsed_ms: Date.now() - start, ...(runId ? { run_id: runId } : {}), ...(code ? { code } : {}) }));
    try {
      if (!timingSafeEqual(expected, digest(req.headers.authorization ?? '')))
        return json(res, 401, { type: 'error', code: 'unauthorized' });
      const path = (req.url ?? '').split('?')[0];
      if (path === '/health' && req.method === 'GET') {
        route = 'health';
        return json(res, 200, { ok: true });
      }
      if (path === '/v1/auth/openai-codex/refresh' && req.method === 'POST') {
        route = 'refresh';
        const body = await readBody(req);
        if (!only(body, ['refresh']) || !text(body.refresh))
          return json(res, 400, { code: 'bad_request' });
        try { return json(res, 200, await authRefresh(body.refresh, refresh)); }
        catch { return json(res, 409, { code: 'refresh_failed' }); }
      }
      const authPath = path.match(/^\/v1\/auth\/openai-codex\/logins(?:\/([a-z0-9-]+))?$/);
      if (authPath && ['POST', 'DELETE'].includes(req.method)) {
        route = 'auth';
        const result = await authenticate(authPath[1], req.method, req.method === 'POST' ? await readBody(req) : undefined);
        return json(res, result.code ? 409 : 200, result.tokens ?? result);
      }
      const capability = path.match(/^\/v1\/models\/([^/]+)\/(.+)$/);
      if (capability && req.method === 'GET') {
        route = 'model';
        const provider = decodeURIComponent(capability[1]), id = decodeURIComponent(capability[2]);
        const model = catalogModel(provider, id);
        return json(res, 200, { in_catalog: !!getModel(provider, id), api: model?.api ?? 'openai-completions', thinking_levels: levels(model) });
      }
      const match = path.match(/^\/v1\/turns\/([^/]+)(?:\/results\/([^/]+))?$/);
      if (match && uuid.test(match[1])) {
        runId = match[1];
        const run = runs.get(runId);
        if (req.method === 'DELETE' && !match[2]) {
          route = 'cancel';
          if (!run) return json(res, 404, { type: 'error', code: 'bad_request' });
          run.cancel('cancelled');
          await run.finished;
          return json(res, 200, { type: 'done', stopReason: 'aborted' });
        }
        if (req.method === 'POST' && match[2]) {
          route = 'result';
          const body = await readBody(req);
          const callId = decodeURIComponent(match[2]);
          if (!run || run.error || !run.pending || run.pending.id !== callId ||
              !only(body, ['result', 'is_error']) || typeof body.is_error !== 'boolean' ||
              !Object.hasOwn(body, 'result')) return json(res, 409, { type: 'error', code: 'bad_request' });
          const pending = run.pending;
          run.pending = undefined;
          // Callback exceptions are fatal; abort before acknowledging to avoid a DELETE race.
          if (body.is_error) run.cancel('cancelled');
          pending.resolve(body);
          return json(res, 200, { ok: true });
        }
      }
      if (path !== '/v1/turns' || req.method !== 'POST')
        return json(res, 400, { type: 'error', code: 'bad_request' });
      route = 'turn';
      const body = await readBody(req);
      if (!validate(body) || runs.has(body.run_id)) return json(res, 400, { type: 'error', code: 'bad_request' });
      const model = resolveModel(body.model);
      runId = body.run_id;
      if (runs.size >= 4 || (body.mode === 'chat' && [...runs.values()].filter(r => r.mode === 'chat').length >= 3))
        return json(res, 503, { type: 'error', code: 'busy' });
      const run = { mode: body.mode, error: undefined, pending: undefined, calls: 0, tools: 0 };
      runs.set(runId, run);
      res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store' });
      res.flushHeaders();
      const send = event => {
        if (event.type === 'error') code = event.code;
        if (!res.destroyed && !res.writableEnded) {
          if (res.writableLength > 1024 * 1024) run.cancel('cancelled');
          else res.write(`${JSON.stringify(event)}\n`);
        }
      };
      let response = {};
      const usedCalls = new Set();
      const m = body.model;
      const currentStart = body.session_state?.messages.filter(m => m.role === 'user').length ?? 0;
      const context = (messages, persist = false) => contextMessages(messages, model, body.tools, currentStart, body.system_prompt, persist);
      const agent = new Agent({
        getApiKey: () => m.api_key || 'not-needed', transport: 'sse', toolExecution: 'sequential',
        streamFn: (model, context, options) => {
          if (run.error || run.calls >= 8) { run.cancel('limit_exceeded'); throw new Error('limit_exceeded'); }
          run.calls++;
          response = {};
          run.signal = options.signal;
          return egress.run({ catalog: getProviders().includes(m.provider), origin: new URL(m.baseUrl).origin, lookup },
            () => streamFn(model, context, { ...options, maxRetries: 0, fetch: async (...args) => {
              let upstream;
              try { upstream = await globalThis.fetch(...args); }
              catch (error) { response.networkError = true; throw error; }
              options.onResponse({ status: upstream.status, headers: Object.fromEntries(upstream.headers) });
              if ([400, 403].includes(upstream.status)) response.code = await errorCode(upstream);
              return upstream;
            } }));
        },
        onResponse: r => {
          if (r.status === 401) run.cancel('model_unauthorized');
          if (r.status === 404) run.cancel('model_not_found');
          response = { ...response, status: r.status, headers: Object.fromEntries(Object.entries(r.headers)
            .filter(([name]) => allowedHeader(name))
            .map(([name, value]) => [name.toLowerCase(), String(value).slice(0, 200)])) };
        },
        // Validation/unknown-tool errors from agent-core can contain raw arguments.
        transformContext: async messages => {
          // Count known submissions rejected by the SDK schema at the same Python validation boundary.
          const current = messages.slice(messages.findLastIndex(m => m.role === 'user'));
          for (const message of current) {
            if (message.role !== 'toolResult' || !message.isError || usedCalls.has(message.toolCallId)) continue;
            const tool = agent.state.tools.find(t => t.name === message.toolName);
            const call = current.flatMap(m => m.role === 'assistant' ? m.content : [])
              .find(c => c.type === 'toolCall' && c.id === message.toolCallId);
            if (tool && call)
              await tool.execute(call.id, object(call.arguments) ? call.arguments : {}, run.signal);
          }
          const safe = messages.map(message => message.role === 'toolResult' && message.isError
            ? { ...message, content: [{ type: 'text', text: TOOL_ERROR }], details: {} } : message);
          return body.version === 2 ? context(safe) : safe;
        },
        initialState: { model, systemPrompt: body.system_prompt, thinkingLevel: m.thinkingLevel ?? 'off',
          messages: body.session_state?.messages ?? (body.history ?? []).map(message => message.role === 'user'
            ? { role: 'user', content: message.text, timestamp: Date.now() }
            : { role: 'assistant', content: [{ type: 'text', text: message.text }], timestamp: Date.now(),
              api: model.api, provider: model.provider, model: model.id, stopReason: 'stop',
              usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } }),
          tools: body.tools.map(t => ({ name: t.name, label: t.name, description: t.description, parameters: t.parameters,
            execute: async (id, args, signal) => {
              if (run.error || run.tools >= 16) { run.cancel('limit_exceeded'); throw new Error(TOOL_ERROR); }
              if (!text(id) || id.length > 200 || usedCalls.has(id)) throw new Error(TOOL_ERROR);
              usedCalls.add(id);
              run.tools++;
              const result = await new Promise((resolve, reject) => {
                const abort = () => { run.pending = undefined; reject(new Error(TOOL_ERROR)); };
                run.pending = { id, resolve: result => { signal.removeEventListener('abort', abort); resolve(result); } };
                signal.addEventListener('abort', abort, { once: true });
                if (signal.aborted) return abort();
                send({ type: 'tool_call', call_id: id, name: t.name, arguments: args });
              });
              if (result.is_error) throw new Error(TOOL_ERROR);
              return { content: [{ type: 'text', text: JSON.stringify(result.result) }], details: {} };
            } })) },
      });
      run.cancel = code => { run.error ??= { code }; agent.abort(); };
      agent.subscribe(event => {
        if (event.type === 'message_update' && event.assistantMessageEvent.type === 'text_delta')
          send({ type: 'text', text: event.assistantMessageEvent.delta });
        if (event.type === 'message_end' && event.message.role === 'assistant' && event.message.stopReason === 'error') {
          const message = event.message.errorMessage ?? '';
          run.error ??= response.status === 429 || response.code === 'rate_limited' ||
            ((!response.status || response.status < 400) && /rate.?limit|usage limit|quota/i.test(message))
            ? { code: 'rate_limited', ...quota(response.headers, message) }
            : { code: upstreamCode(response) };
        }
      });
      const timer = setTimeout(() => run.cancel('deadline'),
        body.mode === 'chat' ? Math.min(deadlineMs, chatDeadlineMs) : deadlineMs);
      res.on('close', () => { if (!res.writableEnded) run.cancel('cancelled'); });
      run.finished = (async () => {
        let local;
        try {
          if (m.provider === 'openai-codex' && m.api_key === 'local-codex') {
            if (!localAuthFile) throw new Error('local_auth_unavailable');
            local = await localCredentials(localAuthFile, localAuthDirectory);
            m.api_key = local.access;
          }
          if (run.error) throw new Error('cancelled');
          await agent.prompt(body.input);
          send(run.error ? { type: 'error', ...run.error } : { type: 'done', stopReason: agent.state.messages.at(-1)?.stopReason ?? 'stop',
            model_calls: run.calls, tool_calls: run.tools,
            ...(body.version === 2 ? { session_state: { version: 1, messages: context(agent.state.messages, true) } } : {}) });
        } catch (error) { send({ type: 'error', ...(run.error ?? { code: error.message === 'local_auth_unavailable' ? error.message : upstreamCode(response) }) }); }
        finally {
          clearTimeout(timer);
          run.pending = undefined;
          runs.delete(runId);
          agent.reset();
          await local?.remove();
          res.end();
        }
      })();
      await run.finished;
    } catch {
      if (!res.headersSent) json(res, 400, { type: 'error', code: 'bad_request' });
      else res.end();
    }
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createPiServer({ token: process.env.PI_SERVICE_TOKEN, deadlineMs: Number(process.env.PI_DEADLINE_MS ?? 120000),
    localAuthFile: process.env.CHATGPT_CODEX_AUTH_FILE, environment: process.env.FASTAPI_ENV,
    chatDeadlineMs: Number(process.env.PI_CHAT_DEADLINE_MS ?? 60000) })
    .listen(Number(process.env.PORT ?? 8081), process.env.PI_HOST ?? '0.0.0.0');
}
