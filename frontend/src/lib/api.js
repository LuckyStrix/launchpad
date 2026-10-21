// Thin wrappers over the backend API. Every call throws ApiError with a readable message.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body, contentType = 'application/json') {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['content-type'] = contentType;
    init.body = contentType === 'application/json' ? JSON.stringify(body) : body;
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Can't reach Launchpad's server.");
  }
  if (!res.ok) {
    let message = res.statusText;
    try {
      const detail = (await res.json()).detail;
      message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    } catch {}
    throw new ApiError(res.status, message);
  }
  return (res.headers.get('content-type') || '').includes('json') ? res.json() : res.text();
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
