// Thin wrappers over the backend API. Every call throws ApiError with a readable message.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// After sleep or a network change the browser can keep using a dead connection, and requests
// on it hang instead of failing. Reads give up after this long so callers can retry; it's above
// the server's own 20 s limit for loading a tile. Writes never time out here: giving up on one
// the server still finishes would invite a repeat (a second workflow run, a second approval).
const READ_TIMEOUT_MS = 25_000;

function timeoutSignal(ms) {
  if (AbortSignal.timeout) return AbortSignal.timeout(ms);
  const controller = new AbortController(); // Safari before 16
  setTimeout(() => controller.abort(new DOMException('Timed out', 'TimeoutError')), ms);
  return controller.signal;
}

function networkError(e) {
  const timedOut = e?.name === 'TimeoutError' || e?.name === 'AbortError';
  return new ApiError(0, timedOut ? "Launchpad's server didn't answer in time." : "Can't reach Launchpad's server.");
}

async function request(method, path, body, contentType = 'application/json') {
  const init = { method, headers: {} };
  if (method === 'GET') init.signal = timeoutSignal(READ_TIMEOUT_MS);
  if (body !== undefined) {
    init.headers['content-type'] = contentType;
    init.body = contentType === 'application/json' ? JSON.stringify(body) : body;
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw networkError(e);
  }
  if (!res.ok) {
    let message = res.statusText;
    try {
      const detail = (await res.json()).detail;
      message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    } catch {}
    throw new ApiError(res.status, message);
  }
  try {
    // The body can still be cut off (or time out) after the headers arrive.
    return await ((res.headers.get('content-type') || '').includes('json') ? res.json() : res.text());
  } catch (e) {
    throw networkError(e);
  }
}

const enc = encodeURIComponent;

export const api = {
  layout: () => request('GET', '/api/layout'),
  saveLayout: (layout, base_revision) => request('PUT', '/api/layout', { layout, base_revision }),
  exportLayout: () => request('GET', '/api/layout/export'),
  importLayout: (yaml) => request('POST', '/api/layout/import', yaml, 'application/yaml'),

  integrations: () => request('GET', '/api/integrations'),
  options: (id, field, q) => request('GET', `/api/integrations/${enc(id)}/options/${enc(field)}?q=${enc(q)}`),
  tileData: (id) => request('GET', `/api/tiles/${enc(id)}/data`),
  tileChanges: () => request('GET', '/api/tiles/changes'),
  action: (id, name, params) => request('POST', `/api/tiles/${enc(id)}/actions/${enc(name)}`, params),

  approvals: () => request('GET', '/api/approvals'),
  decide: (id, approve) => request('POST', `/api/approvals/${enc(id)}`, { approve }),
  activity: () => request('GET', '/api/activity?limit=30'),

  sessions: () => request('GET', '/api/assistant/sessions'),
  session: (id) => request('GET', `/api/assistant/sessions/${enc(id)}`),
  deleteSession: (id) => request('DELETE', `/api/assistant/sessions/${enc(id)}`),
};

// Streams a chat reply. Yields {event, data} for each server-sent event.
export async function* chat(message, sessionId, signal) {
  const res = await fetch('/api/assistant/chat', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail || res.statusText);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end;
    while ((end = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (event && data) yield { event, data: JSON.parse(data) };
    }
  }
}
