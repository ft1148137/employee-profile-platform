const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const string = value => typeof value === 'string';
const size = value => Buffer.byteLength(JSON.stringify(value));
const MAX_STATE = 512 * 1024;

function block(value, role) {
  if (!object(value) || ['textSignature', 'thinkingSignature', 'thoughtSignature']
      .some(key => value[key] !== undefined && !string(value[key]))) return false;
  if (value.type === 'text') return string(value.text);
  if (role !== 'assistant') return value.type === 'image' && string(value.data) && string(value.mimeType);
  if (value.type === 'thinking') return string(value.thinking);
  return value.type === 'toolCall' && string(value.id) && string(value.name) && object(value.arguments);
}

export function validState(state) {
  if (!object(state) || state.version !== 1 || !Array.isArray(state.messages) ||
      Object.keys(state).some(k => !['version', 'messages'].includes(k)) || size(state) > MAX_STATE) return false;
  const pending = new Map();
  for (const m of state.messages) {
    if (!object(m) || !Number.isFinite(m.timestamp)) return false;
    if (m.role === 'user') {
      if (pending.size || !(string(m.content) || Array.isArray(m.content) && m.content.every(b => block(b, m.role)))) return false;
      continue;
    }
    if (!Array.isArray(m.content) || !m.content.every(b => block(b, m.role))) return false;
    if (m.role === 'assistant') {
      if (pending.size || ![m.api, m.provider, m.model].every(v => string(v) && v.length) || !object(m.usage) ||
          !['input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'].every(k => Number.isFinite(m.usage[k])) ||
          !object(m.usage.cost) || !['input', 'output', 'cacheRead', 'cacheWrite', 'total'].every(k => Number.isFinite(m.usage.cost[k])) ||
          !['stop', 'toolUse', 'length'].includes(m.stopReason)) return false;
      for (const b of m.content.filter(b => b.type === 'toolCall')) {
        if (pending.has(b.id)) return false;
        pending.set(b.id, b.name);
      }
    } else if (m.role === 'toolResult') {
      if (!string(m.toolCallId) || !string(m.toolName) || pending.get(m.toolCallId) !== m.toolName || typeof m.isError !== 'boolean') return false;
      pending.delete(m.toolCallId);
    } else return false;
  }
  return pending.size === 0;
}

// Token counts are estimates: one UTF-8 byte per token also covers Chinese text conservatively.
export function contextMessages(messages, model, tools, currentStart, systemPrompt, persist = false) {
  const names = new Set(tools.map(t => t.name));
  const calls = new Map();
  let round = -1;
  const rounds = [];
  for (const message of messages) {
    if (message.role === 'system') continue;
    if (message.role === 'user') { round++; rounds.push([]); calls.clear(); }
    if (!rounds.length) continue;
    let m = message;
    if (m.role === 'assistant') {
      const same = m.api === model.api && m.provider === model.provider && m.model === model.id;
      m = { ...m, content: m.content.flatMap(b => {
        if (b.type === 'thinking' && !same) return [];
        if (b.type === 'toolCall') {
          if (!names.has(b.name)) return [];
          calls.set(b.id, b.name);
        }
        if (same) return [b];
        const { textSignature, thoughtSignature, ...portable } = b;
        return [portable];
      }) };
      if (!m.content.length) continue;
    }
    if (m.role === 'toolResult') {
      if (calls.get(m.toolCallId) !== m.toolName) continue;
      if (m.isError) m = { ...m, content: [{ type: 'text', text: '平台工具调用失败，请检查参数后重试。' }], details: {} };
      if (round < currentStart) {
        let left = 8000;
        m = { ...m, content: m.content.map(b => {
          if (b.type !== 'text') return b;
          const chars = Array.from(b.text);
          const text = chars.slice(0, left).join('');
          const truncated = chars.length > left;
          left = Math.max(0, left - chars.length);
          return { ...b, text: text + (truncated ? '（已截断）' : '') };
        }) };
      }
    }
    rounds.at(-1).push(m);
  }
  const budget = Math.max(0, Math.floor(model.contextWindow / 2) - size(systemPrompt) - size(tools));
  const lengths = rounds.map(r => size(r) - 1);
  let remaining = 1 + lengths.reduce((sum, n) => sum + n, 0);
  const limit = Math.min(budget, MAX_STATE - size({ version: 1, messages: [] }));
  let first = 0;
  while (first < rounds.length - 1 && remaining > limit) remaining -= lengths[first++];
  let result = rounds.slice(first).flat();
  // shortcut: an oversized single round resets context; revisit the state limit if real conversations reach it.
  if (persist && size({ version: 1, messages: result }) > MAX_STATE) result = [];
  return persist ? result : [...messages.filter(m => m.role === 'system'), ...result];
}
