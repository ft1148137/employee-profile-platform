import { normalizeContext } from '@earendil-works/pi-ai';
import { streamSimple as anthropic } from '@earendil-works/pi-ai/api/anthropic-messages';
import { streamSimple as codex } from '@earendil-works/pi-ai/api/openai-codex-responses';
import { streamSimple as completions } from '@earendil-works/pi-ai/api/openai-completions';
import { streamSimple as responses } from '@earendil-works/pi-ai/api/openai-responses';

export { getBuiltinModel as getModel, getBuiltinProviders as getProviders } from '@earendil-works/pi-ai/providers/all';
export { getSupportedThinkingLevels } from '@earendil-works/pi-ai';

const streams = { 'anthropic-messages': anthropic, 'openai-codex-responses': codex,
  'openai-completions': completions, 'openai-responses': responses };

// Dispatch directly with the supplied key; Models auth resolution can refresh OAuth credentials.
export const streamSimple = (model, context, options) => streams[model.api](model, normalizeContext(context), options);
