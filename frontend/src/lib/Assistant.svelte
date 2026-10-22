<script>
  // Chat drawer for Hermes, plus the approval queue for actions Hermes wants to take.
  import DOMPurify from 'dompurify';
  import { marked } from 'marked';
  import { api, chat } from './api.js';

  let { open = $bindable(), approvals = [], ondecide } = $props();

  const SUGGESTIONS = [
    "What's on my calendar today?",
    'Which of my repos have failing CI?',
    'Turn off the living room lights',
    'Add a task to order filament',
  ];

  let sessionId = $state(null);
  let sessions = $state([]);
  let messages = $state([]);
  let draft = $state('');
  let streaming = $state(false);
  let loading = $state(false); // a conversation is loading; sending now would mix it with the old one
  let error = $state('');
  let controller = null;
  let log;
  let input;

  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.tagName === 'A') {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    }
  });
  const render = (text) => DOMPurify.sanitize(marked.parse(text, { breaks: true }));

  $effect(() => {
    if (!open) return;
    refreshSessions();
    queueMicrotask(() => input?.focus());
  });

  $effect(() => {
    messages.length;
    messages.at(-1)?.content;
    queueMicrotask(() => log && (log.scrollTop = log.scrollHeight));
  });

  // Stop any reply still streaming into the conversation we're leaving.
  function stopStream() {
    controller?.abort();
    controller = null;
    streaming = false;
  }

  async function openSession(id) {
    if (!id) return newChat();
    stopStream();
    sessionId = id;
    messages = [];
    error = '';
    loading = true;
    const loaded = await api.session(id).catch((e) => ((error = e.message), []));
    if (sessionId !== id) return; // a slower load for a conversation we've already left
    messages = loaded;
    loading = false;
  }

  function newChat() {
    stopStream();
    loading = false;
    sessionId = null;
    messages = [];
    error = '';
    input?.focus();
  }

  async function send(text = draft) {
    text = text.trim();
    if (!text || streaming || loading) return;
    draft = '';
    error = '';
    messages.push({ role: 'user', content: text });
    messages.push({ role: 'assistant', content: '' });
    const reply = messages.at(-1);
    streaming = true;
    const mine = (controller = new AbortController());
    try {
      for await (const { event, data } of chat(text, sessionId, mine.signal)) {
        if (event === 'session') sessionId = data.id;
        else if (event === 'delta') reply.content += data.text;
        else if (event === 'error') error = data.message;
      }
    } catch (e) {
      if (e.name !== 'AbortError') error = e.message;
    } finally {
      // Remove the empty reply bubble, but only from the conversation it belongs to.
      if (!reply.content && messages.includes(reply)) messages.splice(messages.indexOf(reply), 1);
      if (controller === mine) {
        // Still the current conversation: finish up. (If it was stopped by switching or
        // deleting, whoever stopped it owns the session list now.)
        controller = null;
        streaming = false;
        refreshSessions();
      }
    }
  }

  function keydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  function refreshSessions() {
    api.sessions().then((list) => (sessions = list)).catch(() => {});
  }

  async function removeSession() {
    if (!sessionId) return;
    const id = sessionId;
    newChat(); // stop any reply first, so it isn't saved into the deleted conversation
    try {
      await api.deleteSession(id);
    } catch (e) {
      error = `Couldn't delete that conversation: ${e.message}`;
    }
    refreshSessions();
  }

  const describe = (args) => Object.entries(args).filter(([, v]) => v !== '' && v != null);
</script>

<svelte:window onkeydown={(e) => open && e.key === 'Escape' && (open = false)} />

<aside class="drawer" class:open aria-label="Assistant" inert={!open}>
  <header>
    <h2>Hermes</h2>
    <select
      class="input sessions"
      aria-label="Conversation"
      value={sessionId ?? ''}
      onchange={(e) => openSession(e.currentTarget.value)}
    >
      <option value="">New conversation</option>
      {#each sessions as s (s.id)}
        <option value={s.id}>{s.title}</option>
      {/each}
    </select>
    {#if sessionId}
      <button class="btn quiet" onclick={removeSession} title="Delete this conversation">Delete</button>
    {/if}
    <button class="btn quiet" onclick={() => (open = false)} aria-label="Close assistant">Close</button>
  </header>

  {#if approvals.length}
    <section class="approvals" aria-label="Waiting for your approval">
      {#each approvals as request (request.id)}
        <div class="approval">
          <p><strong>{request.title ?? `Hermes wants to run ${request.tool.replaceAll('_', ' ')}`}</strong></p>
          <dl>
            {#each describe(request.args) as [key, value] (key)}
              <dt>{key}</dt>
              <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
            {/each}
          </dl>
          <div class="approval-actions">
            <button class="btn" onclick={() => ondecide(request.id, false)}>Deny</button>
            <button class="btn primary" onclick={() => ondecide(request.id, true)}>Approve</button>
          </div>
        </div>
      {/each}
    </section>
  {/if}

  <div class="log" bind:this={log} aria-live="polite">
    {#if !messages.length}
      <div class="intro">
        <p>Ask Hermes about your calendar, tasks, repos or home. Anything that changes something asks you first.</p>
        <div class="suggestions">
          {#each SUGGESTIONS as suggestion (suggestion)}
            <button class="btn" onclick={() => send(suggestion)}>{suggestion}</button>
          {/each}
        </div>
      </div>
    {/if}
    {#each messages as message, i (i)}
      <div class="message {message.role}">
        {#if message.role === 'assistant'}
          {#if message.content}
            <!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized with DOMPurify -->
            {@html render(message.content)}
          {:else}
            <p class="thinking">Working on it…</p>
          {/if}
        {:else}
          <p>{message.content}</p>
        {/if}
      </div>
    {/each}
    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </div>

  <form
    class="compose"
    onsubmit={(e) => {
      e.preventDefault();
      send();
    }}
  >
    <label class="visually-hidden" for="assistant-input">Message Hermes</label>
    <textarea
      id="assistant-input"
      class="input"
      rows="2"
      placeholder="Message Hermes"
      bind:value={draft}
      bind:this={input}
      onkeydown={keydown}
    ></textarea>
    {#if streaming}
      <button type="button" class="btn" onclick={() => controller?.abort()}>Stop</button>
    {:else}
      <button type="submit" class="btn primary" disabled={!draft.trim() || loading}>Send</button>
    {/if}
  </form>
</aside>

<style>
  .drawer {
    position: fixed;
    z-index: 20;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(27rem, 100vw);
    display: flex;
    flex-direction: column;
    background: var(--drawer);
    border-left: 1px solid var(--line);
    box-shadow: -12px 0 40px #1d242226;
    transform: translateX(105%);
    transition: transform 200ms ease;
  }
  .drawer.open {
    transform: none;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--line);
  }
  h2 {
    margin: 0 0.4rem 0 0;
    font-size: var(--step-1);
    font-stretch: 125%;
    font-weight: 750;
  }
  .sessions {
    flex: 1;
    min-width: 0;
    padding: 0.35em 0.5em;
    font-size: var(--step--1);
  }

  .approvals {
    display: grid;
    gap: 0.5rem;
    padding: 0.75rem 1rem;
    background: color-mix(in srgb, var(--tape) 16%, var(--drawer));
    border-bottom: 1px solid var(--line);
  }
  .approval p {
    margin: 0 0 0.3rem;
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.1rem 0.75rem;
    margin: 0 0 0.5rem;
    font-size: var(--step--1);
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
  }
  .approval-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.4rem;
  }

  .log {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .intro p {
    margin: 0 0 0.75rem;
    color: var(--muted);
    max-width: 34ch;
  }
  .suggestions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .suggestions .btn {
    white-space: normal;
    text-align: left;
    font-weight: 450;
  }
  .message {
    max-width: 92%;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .message :global(p) {
    margin: 0 0 0.5em;
  }
  .message :global(p:last-child) {
    margin-bottom: 0;
  }
  .message :global(pre) {
    overflow-x: auto;
    padding: 0.6rem;
    background: var(--holder);
    border-radius: 4px;
    font-size: var(--step--1);
  }
  .message.user {
    align-self: flex-end;
    padding: 0.5rem 0.8rem;
    background: var(--holder);
    border-radius: 10px 10px 2px 10px;
  }
  .message.user p {
    margin: 0;
    white-space: pre-wrap;
  }
  .thinking {
    color: var(--muted);
  }
  .error {
    margin: 0;
    padding: 0.5rem 0.7rem;
    border-left: 3px solid var(--error);
    background: color-mix(in srgb, var(--error) 10%, transparent);
    font-size: var(--step--1);
  }
  .compose {
    display: flex;
    gap: 0.5rem;
    align-items: flex-end;
    padding: 0.75rem 1rem 1rem;
    border-top: 1px solid var(--line);
  }
  .compose textarea {
    flex: 1;
    resize: none;
  }
</style>
