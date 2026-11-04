import { loginOpenAICodex } from '@mariozechner/pi-ai/oauth';
import { randomUUID } from 'node:crypto';
import { egress } from './egress.mjs';

export function authLogins(login = loginOpenAICodex, lifetime = 900000) {
  let active;
  return async (id, method, body) => {
    if (!id && method === 'POST') {
      if (active) return { code: 'login_busy' };
      const slot = { id: randomUUID() };
      active = slot;
      const abort = new AbortController();
      const input = new Promise((resolve, reject) => { slot.submit = resolve; slot.cancel = reject; });
      // A cancellation may arrive before Pi asks for manual input.
      input.catch(() => {});
      const cancel = slot.cancel;
      slot.cancel = error => { abort.abort(); cancel(error); };
      const timer = setTimeout(() => slot.cancel(new Error('cancelled')), lifetime);
      slot.done = new Promise(resolve => {
        slot.started = resolve;
      });
      slot.result = egress.run({ catalog: false, origin: 'https://auth.openai.com', signal: abort.signal }, () => login({
        onAuth: ({ url }) => slot.started({ login_id: slot.id, url }),
        onManualCodeInput: () => input,
        onPrompt: () => input,
      })).then(tokens => ({ tokens }), () => ({ code: 'login_failed' })).finally(() => {
        clearTimeout(timer);
        if (active === slot) active = undefined;
      });
      return Promise.race([slot.done, slot.result]);
    }
    const slot = active;
    if (!slot || slot.id !== id || (slot.consumed && method !== 'DELETE')) return { code: 'login_not_found' };
    slot.consumed = true;
    if (method === 'DELETE') slot.cancel(new Error('cancelled'));
    else if (method === 'POST' && typeof body?.input === 'string') slot.submit(body.input);
    else { slot.consumed = false; return { code: 'login_failed' }; }
    const result = await slot.result;
    return method === 'DELETE' ? { ok: true } : result;
  };
}
