<script>
  import { onMount } from 'svelte';
  import { api } from './lib/api.js';
  import Assistant from './lib/Assistant.svelte';
  import {
    board,
    change,
    discard,
    importYaml,
    load,
    newId,
    save,
    startEditing,
    tileLinks,
    undo,
  } from './lib/board.svelte.js';
  import Group from './lib/Group.svelte';
  import TileEditor from './lib/TileEditor.svelte';

  const APPROVAL_POLL_MS = 3000;

  let query = $state('');
  let search;
  let fileInput = $state();
  let editorTarget = $state(null);
  let assistantOpen = $state(location.hash === '#assistant');
  let approvals = $state([]);
  let notice = $state(noticeFromUrl());
  let now = $state(new Date());

  function noticeFromUrl() {
    const google = new URLSearchParams(location.search).get('google');
    if (google === 'connected') return 'Google account connected.';
    if (google === 'error') {
      const reason = new URLSearchParams(location.search).get('reason');
      return `Google sign-in didn't finish${reason ? ` (${reason})` : ''}. Try connecting again from the tile.`;
    }
    return '';
  }

  onMount(() => {
    load();
    if (location.search) history.replaceState(null, '', location.pathname + location.hash);
    const clock = setInterval(() => (now = new Date()), 15_000);
    const poll = setInterval(pollApprovals, APPROVAL_POLL_MS);
    pollApprovals();
    return () => {
      clearInterval(clock);
      clearInterval(poll);
    };
  });

  async function pollApprovals() {
    if (document.hidden) return;
    approvals = await api.approvals().catch(() => approvals);
  }

  async function decide(id, approve) {
    approvals = approvals.filter((a) => a.id !== id);
    await api.decide(id, approve).catch((e) => (notice = e.message));
  }

  $effect(() => {
    const hash = assistantOpen ? '#assistant' : '';
    if (location.hash !== hash) history.replaceState(null, '', location.pathname + hash);
  });

  // Search: filter tiles by name, integration or URL; Enter opens the first match.
  const terms = $derived(query.trim().toLowerCase());
  const matches = $derived((tile) => {
    const schema = board.integrations[tile.integration];
    return [tile.title, schema?.name, tile.config?.url].some((v) => v?.toLowerCase().includes(terms));
  });
  const filter = $derived(terms && !board.editing ? matches : null);

  function openFirstMatch(event) {
    if (!terms) return;
    const tile = board.layout.groups.flatMap((g) => g.tiles).find(matches);
    const href = tile && (tileLinks[tile.id] ?? tile.config?.url);
    if (!href) return;
    if (href === '#assistant') return (assistantOpen = true);
    if (event.ctrlKey || event.metaKey) window.open(href, '_blank');
    else location.href = href;
  }

  function globalKeys(event) {
    const typing = event.target.closest('input, textarea, select, [contenteditable]');
    if (event.key === '/' && !typing && !board.editing) {
      event.preventDefault();
      search.focus();
    }
  }

  function addGroup() {
    change((layout) => layout.groups.push({ id: newId(), title: 'New group', tiles: [] }));
  }

  async function exportLayout() {
    const yaml = await api.exportLayout();
    const link = Object.assign(document.createElement('a'), {
      href: URL.createObjectURL(new Blob([yaml], { type: 'application/yaml' })),
      download: 'layout.yaml',
    });
    link.click();
    URL.revokeObjectURL(link.href);
  }

  async function importLayout(event) {
    const file = event.currentTarget.files[0];
    event.currentTarget.value = '';
    if (!file || !confirm(`Replace the whole layout with ${file.name}? You can restore the old one from history.`)) return;
    try {
      await importYaml(await file.text());
      notice = 'Layout imported.';
    } catch (e) {
      notice = e.message;
    }
  }

  const clock = $derived(
    now.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }),
  );
</script>

<svelte:window
  onkeydown={globalKeys}
  onbeforeunload={(e) => board.dirty && e.preventDefault()}
  onhashchange={() => location.hash === '#assistant' && (assistantOpen = true)}
/>

<div class="page" class:pegboard={board.editing}>
  <header class="bar">
    <h1>Launchpad</h1>
    <div class="search">
      <label class="visually-hidden" for="search">Find a tile</label>
      <input
        id="search"
        class="input"
        type="search"
        placeholder="Find a tile (press /)"
        autocomplete="off"
        disabled={board.editing}
        bind:value={query}
        bind:this={search}
        onkeydown={(e) => {
          if (e.key === 'Enter') openFirstMatch(e);
          if (e.key === 'Escape') query = '';
        }}
      />
    </div>
    <time class="clock" datetime={now.toISOString()}>{clock}</time>
    {#if !board.editing}
      <button class="btn" onclick={startEditing}>Edit</button>
    {/if}
    <button class="btn assistant-btn" onclick={() => (assistantOpen = !assistantOpen)} aria-expanded={assistantOpen}>
      Hermes
      {#if approvals.length}
        <span class="badge" aria-label="{approvals.length} waiting for approval">{approvals.length}</span>
      {/if}
    </button>
  </header>

  {#if notice}
    <div class="notice" role="status">
      <span>{notice}</span>
      <button class="btn quiet" onclick={() => (notice = '')}>Dismiss</button>
    </div>
  {/if}
  {#if board.error}
    <div class="notice error" role="alert">
      <span>{board.error}</span>
      <button class="btn quiet" onclick={load}>Reload</button>
    </div>
  {/if}

  <main>
    {#each board.layout.groups as group, index (group.id)}
      <Group
        {group}
        {index}
        {filter}
        onaddtile={(groupId) => (editorTarget = { groupId })}
        onedittile={(tileId) => (editorTarget = { tileId })}
        onopenassistant={() => (assistantOpen = true)}
      />
    {/each}

    {#if !board.layout.groups.length && !board.error}
      <div class="empty">
        <p>Your board is empty. Add a group, then put your first tiles in it.</p>
        <button
          class="btn primary"
          onclick={() => {
            startEditing();
            addGroup();
          }}>Start a group</button
        >
      </div>
    {:else if filter && !board.layout.groups.some((g) => g.tiles.some(filter))}
      <p class="empty">No tiles match “{query}”.</p>
    {/if}

    {#if board.editing}
      <button class="add-group" onclick={addGroup}>Add a group</button>
    {/if}
  </main>
</div>

{#if board.editing}
  <div class="edit-bar" role="toolbar" aria-label="Layout editing">
    <span class="state">{board.dirty ? 'Unsaved changes' : 'Editing layout'}</span>
    <button class="btn quiet" onclick={undo} disabled={!board.undoCount}>Undo</button>
    <button class="btn quiet" onclick={exportLayout}>Export</button>
    <button class="btn quiet" onclick={() => fileInput.click()}>Import</button>
    <input type="file" accept=".yaml,.yml" hidden bind:this={fileInput} onchange={importLayout} />
    <span class="spacer"></span>
    <button class="btn" onclick={discard}>{board.dirty ? 'Discard' : 'Done'}</button>
    <button class="btn primary" onclick={save} disabled={!board.dirty || board.saving}>
      {board.saving ? 'Saving…' : 'Save layout'}
    </button>
  </div>
{/if}

{#if editorTarget}
  <TileEditor target={editorTarget} onclose={() => (editorTarget = null)} />
{/if}

<Assistant bind:open={assistantOpen} {approvals} ondecide={decide} />

<style>
  .page {
    min-height: 100vh;
    padding: 0 clamp(1rem, 4vw, 3rem) 6rem;
    transition: background-color 300ms ease;
  }
  /* Edit mode: the pegboard behind the drawers shows through. */
  .pegboard {
    background-image: radial-gradient(circle, var(--peg) 1.6px, transparent 2px);
    background-size: 22px 22px;
    background-position: 11px 11px;
    animation: peg-in 260ms ease-out;
  }
  @keyframes peg-in {
    from {
      background-size: 30px 30px;
      opacity: 0.6;
    }
  }

  .bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.75rem 1rem;
    padding: 1.25rem 0 1.5rem;
  }
  h1 {
    margin: 0;
    font-size: var(--step-2);
    font-weight: 800;
    font-stretch: 125%;
    letter-spacing: -0.01em;
    line-height: 1;
  }
  .search {
    flex: 1 1 14rem;
    max-width: 30rem;
  }
  .search .input {
    border-radius: 999px;
    padding-inline: 1rem;
    background: var(--drawer);
  }
  .clock {
    margin-left: auto;
    color: var(--muted);
    font-size: var(--step--1);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .assistant-btn {
    position: relative;
  }
  .badge {
    display: inline-grid;
    place-items: center;
    min-width: 1.2rem;
    height: 1.2rem;
    padding: 0 0.3rem;
    border-radius: 999px;
    background: var(--tape);
    color: var(--tape-ink);
    font-size: 0.7rem;
    font-weight: 750;
  }

  main {
    display: grid;
    gap: 2rem;
  }

  .notice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.25rem;
    padding: 0.5rem 0.5rem 0.5rem 1rem;
    border-left: 3px solid var(--ok);
    background: var(--drawer);
    border-radius: 4px;
  }
  .notice.error {
    border-left-color: var(--error);
  }

  .empty {
    color: var(--muted);
  }
  .empty p {
    margin: 0 0 0.75rem;
  }

  .add-group {
    padding: 1.1rem;
    border: 2px dashed var(--muted);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--drawer) 60%, transparent);
    color: var(--muted);
    font-weight: 650;
    cursor: pointer;
  }
  .add-group:hover {
    border-color: var(--tape);
    color: var(--ink);
  }

  .edit-bar {
    position: fixed;
    z-index: 10;
    left: 50%;
    bottom: 1rem;
    transform: translateX(-50%);
    width: min(48rem, calc(100vw - 2rem));
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem;
    padding: 0.5rem 0.5rem 0.5rem 1rem;
    background: var(--drawer);
    border: 2px solid var(--tape);
    border-radius: 999px;
    box-shadow: 0 10px 30px #1d242233;
  }
  .state {
    font-size: var(--step--1);
    font-weight: 650;
    margin-right: 0.5rem;
  }
  .spacer {
    flex: 1;
  }

  @media (max-width: 40rem) {
    .clock {
      display: none;
    }
    .search {
      order: 5;
      flex-basis: 100%;
      max-width: none;
    }
    .edit-bar {
      border-radius: 14px;
    }
  }
</style>
