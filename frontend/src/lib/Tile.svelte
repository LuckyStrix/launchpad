<script>
  // Renders any integration's TileData generically: readout, detail, item list and controls.
  import { api } from './api.js';
  import { board, MAX_SPAN, sizeName, sizeOf, tileLinks } from './board.svelte.js';
  import Icon from './Icon.svelte';

  let { tile, onedit, onremove, onresize, onopenassistant } = $props();

  const STATUS_LABEL = { ok: 'Working', warn: 'Needs attention', error: 'Problem', idle: 'Idle' };

  const schema = $derived(board.integrations[tile.integration]);
  const title = $derived(tile.title || schema?.name || tile.integration);
  let data = $state(null);
  let note = $state('');
  let failed = $state(false); // the last data fetch failed (not just "not saved yet")
  let busy = $state(false);

  async function refresh() {
    try {
      data = await api.tileData(tile.id);
      ok();
    } catch (e) {
      data = null;
      fail(e);
    }
  }

  function ok() {
    note = '';
    failed = false;
  }

  // A 404 means the tile isn't saved yet, which isn't a fault.
  function fail(e) {
    failed = e.status !== 404;
    note = e.status === 404 ? 'Save the layout to load this tile.' : e.message;
  }

  $effect(() => {
    // Re-fetch when this tile's config changes or a new layout is saved.
    JSON.stringify(tile.config);
    board.revision;
    refresh();
    const timer = setInterval(() => document.hidden || refresh(), (schema?.refresh ?? 60) * 1000);
    return () => clearInterval(timer);
  });

  async function run(action, params = {}) {
    busy = true;
    try {
      data = await api.action(tile.id, action, params);
      ok();
    } catch (e) {
      fail(e);
    } finally {
      busy = false;
    }
  }

  const href = $derived(data?.url ?? tile.config?.url ?? null);
  $effect(() => {
    tileLinks[tile.id] = href;
  });

  const status = $derived(!schema || failed ? 'error' : (data?.status ?? 'idle'));
  const dims = $derived(sizeOf(tile.size));
  // Room for list rows: none on a single small cell, about three per extra row of height.
  const itemRoom = $derived(dims[1] === 1 ? (dims[0] > 1 ? 2 : 0) : 3 * dims[1] - 1 + (dims[0] > 1 ? 2 : 0));
  const items = $derived((data?.items ?? []).slice(0, itemRoom));
  const controls = $derived((data?.controls ?? []).slice(0, dims[0] * dims[1] === 1 ? 2 : 6 * dims[0]));

  // Resize by dragging the corner grip (one undo point per drag) or with the arrow keys.
  let article;
  function startResize(event) {
    event.preventDefault();
    const cell = article.parentElement;
    const rect = cell.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(cell.parentElement).columnGap) || 0;
    const [w0, h0] = dims;
    const shown = Number(getComputedStyle(cell).gridColumnEnd.replace('span', '')) || w0;
    const stepX = (rect.width + gap) / shown;
    const stepY = (rect.height + gap) / h0;
    const x0 = event.clientX;
    const y0 = event.clientY;
    let recorded = false;
    const move = (e) => {
      const next = sizeName(w0 + Math.round((e.clientX - x0) / stepX), h0 + Math.round((e.clientY - y0) / stepY));
      if (next === tile.size) return;
      onresize?.(next, !recorded);
      recorded = true;
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  const KEY_STEP = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] };
  function keyResize(event) {
    const step = KEY_STEP[event.key];
    if (!step) return;
    event.preventDefault();
    const next = sizeName(dims[0] + step[0], dims[1] + step[1]);
    if (next !== tile.size) onresize?.(next, true);
  }

  // svelte-dnd-action starts a tile drag on mousedown/touchstart; the grip must not.
  function noDrag(node) {
    const stop = (e) => e.stopPropagation();
    node.addEventListener('mousedown', stop);
    node.addEventListener('touchstart', stop);
    return { destroy: () => (node.removeEventListener('mousedown', stop), node.removeEventListener('touchstart', stop)) };
  }

  function open(event) {
    if (href === '#assistant') {
      event.preventDefault();
      onopenassistant?.();
    }
  }
</script>

<article class="tile" class:tall={dims[1] > 1} bind:this={article} class:editing={board.editing} class:busy aria-busy={busy}>
  <header class="holder">
    <Icon icon={tile.icon} fallback={schema?.icon} url={tile.config?.url} label={title} />
    <h3>{title}</h3>
    <span class="lamp {status}" title={STATUS_LABEL[status]}></span>
    <span class="visually-hidden">{STATUS_LABEL[status]}</span>
  </header>

  <div class="body">
    {#if !schema}
      <p class="message">The {tile.integration} integration isn't installed.</p>
    {:else if data?.error}
      <p class="message">{data.error}</p>
    {:else if note}
      <p class="message">{note}</p>
    {:else if data}
      {#if data.value}<p class="value">{data.value}</p>{/if}
      {#if data.detail}<p class="detail">{data.detail}</p>{/if}
      {#if items.length}
        <ul class="items">
          {#each items as item, i (i)}
            <li>
              {#if item.status}<span class="lamp {item.status}"></span>{/if}
              <span class="item-text">
                {#if item.url && !board.editing}
                  <a href={item.url} class="raised">{item.label}</a>
                {:else}
                  {item.label}
                {/if}
                {#if item.detail}<small>{item.detail}</small>{/if}
              </span>
              {#if item.action && !board.editing}
                {#if item.kind === 'switch'}
                  <button
                    class="switch small raised"
                    role="switch"
                    aria-checked={!!item.on}
                    aria-label={item.label}
                    disabled={busy}
                    onclick={() => run(item.action, item.params)}
                  ><span></span></button>
                {:else if item.kind === 'button'}
                  <button class="btn row-btn raised" aria-label="{item.action_label}: {item.label}" disabled={busy}
                          onclick={() => run(item.action, item.params)}>{item.action_label}</button>
                {:else}
                  <button
                    class="check raised"
                    title={item.action_label}
                    aria-label="{item.action_label}: {item.label}"
                    disabled={busy}
                    onclick={() => run(item.action, item.params)}
                  ></button>
                {/if}
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </div>

  {#if controls.length && !board.editing && !data?.error}
    <div class="controls">
      {#each controls as control, i (i)}
        {#if control.kind === 'toggle'}
          <button
            class="switch raised"
            role="switch"
            aria-checked={!!control.value}
            aria-label={control.label}
            disabled={busy}
            onclick={() => run(control.action, { ...control.params, value: !control.value })}
          ><span></span></button>
        {:else if control.kind === 'slider'}
          <input
            class="slider raised"
            type="range"
            aria-label={control.label}
            min={control.min}
            max={control.max}
            step={control.step}
            value={control.value}
            disabled={busy}
            onchange={(e) => run(control.action, { ...control.params, value: Number(e.currentTarget.value) })}
          />
        {:else}
          <button class="btn raised" disabled={busy} onclick={() => run(control.action, control.params)}>
            {control.label}
          </button>
        {/if}
      {/each}
    </div>
  {/if}

  {#if href && !board.editing}
    <a class="cover" {href} onclick={open}><span class="visually-hidden">Open {title}</span></a>
  {/if}

  {#if board.editing}
    <div class="tools">
      <button class="btn" onclick={onedit}>Settings</button>
      <button class="btn quiet danger" onclick={onremove} aria-label="Remove {title}" title="Remove">✕</button>
      <button
        class="btn grip"
        use:noDrag
        onpointerdown={startResize}
        onkeydown={keyResize}
        aria-label="Resize {title}: {dims[0]} wide, {dims[1]} tall. Drag, or use the arrow keys (up to {MAX_SPAN})."
        title="Drag to resize"
      >
        <span class="size">{dims[0]}×{dims[1]}</span>
        <svg viewBox="0 0 10 10" aria-hidden="true"><path d="M9 3 3 9M9 6 6 9" /></svg>
      </button>
    </div>
  {/if}
</article>

<style>
  .tile {
    container: tile / inline-size;
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    height: 100%;
    min-width: 0;
    padding: 0.5rem 0.6rem 0.7rem;
    background: var(--drawer);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    transition: transform 120ms ease, box-shadow 120ms ease;
  }
  .tile:has(.cover:hover) {
    transform: translateY(-1px);
    box-shadow: var(--shadow), 0 6px 14px #1d242214;
  }
  .tile.busy {
    opacity: 0.75;
  }
  .tile.editing {
    cursor: grab;
    border-style: dashed;
    border-color: var(--muted);
  }

  /* The label holder: a recessed frame that holds the name, like a drawer's label slot. */
  .holder {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.35rem 0.5rem;
    background: var(--holder);
    border-radius: 3px;
    box-shadow: inset 0 1px 2px #1d242226;
  }
  h3 {
    flex: 1;
    min-width: 0;
    margin: 0;
    font-size: var(--step--1);
    font-weight: 650;
    letter-spacing: 0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    padding: 0 0.15rem;
  }
  .value {
    margin: 0;
    font-size: var(--step-2);
    font-weight: 700;
    font-stretch: 82%;
    line-height: 1.05;
    font-variant-numeric: tabular-nums;
    overflow-wrap: anywhere;
  }
  .tall .value {
    font-size: var(--step-3);
  }
  .detail,
  .message {
    margin: 0;
    color: var(--muted);
    font-size: var(--step--1);
    line-height: 1.35;
    overflow-wrap: anywhere;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .message {
    -webkit-line-clamp: 4;
    line-clamp: 4;
  }

  .items {
    list-style: none;
    margin: 0.4rem 0 0;
    padding: 0;
    display: grid;
    gap: 0.1rem;
    font-size: var(--step--1);
  }
  .items li {
    display: flex;
    align-items: baseline;
    gap: 0.45rem;
    padding: 0.25rem 0;
    border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  }
  .items .lamp {
    transform: translateY(-0.05rem);
  }
  .item-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    column-gap: 0.5rem;
  }
  .item-text a {
    text-decoration: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }
  .item-text a:hover {
    text-decoration: underline;
  }
  .item-text small {
    color: var(--muted);
  }
  .check {
    flex: none;
    width: 1.05rem;
    height: 1.05rem;
    border: 1.5px solid var(--muted);
    border-radius: 50%;
    background: transparent;
    cursor: pointer;
    align-self: center;
  }
  .check:hover {
    background: var(--ok);
    border-color: var(--ok);
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem;
  }
  .switch {
    width: 2.6rem;
    height: 1.5rem;
    padding: 2px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--holder);
    cursor: pointer;
  }
  .switch span {
    display: block;
    width: 1.15rem;
    height: 1.15rem;
    border-radius: 50%;
    background: var(--muted);
    transition: transform 140ms ease;
  }
  .switch[aria-checked='true'] {
    background: var(--ok);
    border-color: var(--ok);
  }
  .switch.small {
    flex: none;
    width: 2.1rem;
    height: 1.2rem;
  }
  .switch.small span {
    width: 0.9rem;
    height: 0.9rem;
  }
  .switch.small[aria-checked='true'] span {
    transform: translateX(0.9rem);
  }
  .row-btn {
    flex: none;
    padding: 0.2em 0.6em;
    font-size: 0.75rem;
  }
  .switch[aria-checked='true'] span {
    transform: translateX(1.1rem);
    background: #fff;
  }
  .slider {
    flex: 1;
    min-width: 5rem;
    accent-color: var(--ok);
  }

  /* Stretched link: the whole drawer opens the tile's URL; controls sit above it. */
  .cover {
    position: absolute;
    inset: 0;
    z-index: 1;
    border-radius: inherit;
  }
  .raised {
    position: relative;
    z-index: 2;
  }

  /* Edit tools sit on a strip over the bottom of the drawer, whatever the tile size. */
  .tools {
    position: absolute;
    z-index: 3;
    left: 0;
    right: 0;
    bottom: 0;
    display: flex;
    gap: 0.25rem;
    padding: 0.4rem;
    background: var(--drawer);
    border-top: 1px dashed var(--line);
  }
  .tools .btn {
    padding: 0.3em 0.65em;
    font-size: 0.75rem;
  }
  .tools .danger {
    margin-left: auto;
  }
  .grip {
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    cursor: nwse-resize;
    touch-action: none;
  }
  /* On narrow tiles the strip only has room for the grip itself. */
  @container tile (max-width: 12rem) {
    .grip .size {
      display: none;
    }
  }
  .grip svg {
    width: 0.7rem;
    height: 0.7rem;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
    fill: none;
  }
</style>
