import { createProvider } from '@earendil-works/pi-ai';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';

export const CODING_TOOLS = new Set(['read', 'write', 'edit', 'bash', 'grep', 'glob']);

// The private upstream provider is selected by the trusted broker. Flue sees an alias.
export function createGateway(baseUrl, role) {
  const url = new URL(baseUrl);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1'
      || url.pathname !== '/v1' || url.username || url.password || url.search || url.hash) {
    throw new Error('Expected the local model broker');
  }
  if (!['coder', 'reviewer'].includes(role)) throw new Error('Unknown agent role');
  const api = openAICompletionsApi();
  const options = (supplied = {}) => ({
    ...supplied, temperature: 0, maxTokens: 4096, timeoutMs: 45_000,
    maxRetries: 0, cacheRetention: 'none',
    fetch: (input, init) => {
      if (String(input) !== `${baseUrl}/chat/completions`) {
        throw new Error('Unexpected model route');
      }
      return fetch(input, { ...init, redirect: 'error' });
    },
  });
  const context = (value) => ({
    ...value,
    tools: role === 'coder' ? (value.tools ?? []).filter(tool => CODING_TOOLS.has(tool.name)) : [],
  });
  return createProvider({
    id: 'model-gateway',
    auth: { apiKey: { name: 'Local broker', resolve: async () => ({ auth: { apiKey: 'broker-local' } }) } },
    models: [{
      id: 'worker', name: 'Worker', provider: 'model-gateway', api: 'openai-completions',
      baseUrl, reasoning: false, input: ['text'], contextWindow: 0, maxTokens: 4096,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      compat: { supportsStore: false, supportsDeveloperRole: false,
        supportsReasoningEffort: false, supportsUsageInStreaming: true, maxTokensField: 'max_tokens' },
    }],
    api: {
      stream: (model, value, supplied) => api.stream(model, context(value), options(supplied)),
      streamSimple: (model, value, supplied) => api.streamSimple(model, context(value), options(supplied)),
    },
  });
}
