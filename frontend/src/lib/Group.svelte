<script>
  import { dndzone, SHADOW_ITEM_MARKER_PROPERTY_NAME } from 'svelte-dnd-action';
  import { flip } from 'svelte/animate';
  import { board, change, checkpoint, drag, sizeOf } from './board.svelte.js';
  import Tile from './Tile.svelte';

  let { group, index, filter = null, onaddtile, onedittile, onopenassistant } = $props();

  const FLIP_MS = 160;

  // How many columns the grid has right now, so a wide tile never spans more than fit (phones).
  let grid = $state();
  let cols = $state(6);
  $effect(() => {
    if (!grid) return;
    const measure = () => (cols = getComputedStyle(grid).gridTemplateColumns.split(' ').length || 1);
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    return () => observer.disconnect();
  });

  function span(size) {
    const [w, h] = sizeOf(size);
    return `grid-column: span ${Math.min(w, cols)}; grid-row: span ${h}`;
  }

  const visible = $derived(filter ? group.tiles.filter(filter) : group.tiles);

  function consider(event) {
    if (!drag.active) {
      checkpoint();
      drag.active = true;
    }
    group.tiles = event.detail.items;
  }

  function finalize(event) {
    group.tiles = event.detail.items;
    drag.active = false;
  }

  const groupAt = (layout) => layout.groups.find((g) => g.id === group.id);

  function move(delta) {
    change((layout) => {
      const [moved] = layout.groups.splice(index, 1);
      layout.groups.splice(index + delta, 0, moved);
    });
  }

  function rename(title) {
    change((layout) => (groupAt(layout).title = title));
  }

  function removeGroup() {
    change((layout) => layout.groups.splice(index, 1));
  }

  function removeTile(tileId) {
    change((layout) => {
      const g = groupAt(layout);
      g.tiles = g.tiles.filter((t) => t.id !== tileId);
    });
  }

  // `record` is true for the first step of a resize drag, so the whole drag is one undo point.
  function resize(tileId, size, record) {
    if (record) checkpoint();
    groupAt(board.layout).tiles.find((t) => t.id === tileId).size = size;
  }
</script>

{#if board.editing || visible.length}
  <section class="group" aria-labelledby="group-{group.id}">
    <header>
      {#if board.editing}
        <label class="visually-hidden" for="group-{group.id}">Group name</label>
        <input
          id="group-{group.id}"
          class="title-input"
          value={group.title}
          onchange={(e) => rename(e.currentTarget.value.trim() || 'Untitled')}
        />
        <div class="group-tools">
          <button class="btn" onclick={() => onaddtile(group.id)}>Add tile</button>
          <button class="btn quiet" onclick={() => move(-1)} disabled={index === 0} aria-label="Move group up">↑</button>
          <button
            class="btn quiet"
            onclick={() => move(1)}
            disabled={index === board.layout.groups.length - 1}
            aria-label="Move group down">↓</button
          >
          <button class="btn quiet danger" onclick={removeGroup}>Delete group</button>
        </div>
      {:else}
        <h2 id="group-{group.id}">{group.title}</h2>
      {/if}
    </header>

    {#if board.editing}
      <div
        class="grid"
        bind:this={grid}
        use:dndzone={{ items: group.tiles, flipDurationMs: FLIP_MS, type: 'tile', dropTargetStyle: {} }}
        onconsider={consider}
        onfinalize={finalize}
      >
        {#each group.tiles as tile (tile.id)}
          <div class="cell" style={span(tile.size)} animate:flip={{ duration: FLIP_MS }}>
            {#if tile[SHADOW_ITEM_MARKER_PROPERTY_NAME]}
              <div class="slot"></div>
            {:else}
              <Tile
                {tile}
                onedit={() => onedittile(tile.id)}
                onremove={() => removeTile(tile.id)}
                onresize={(size, record) => resize(tile.id, size, record)}
              />
            {/if}
          </div>
        {/each}
      </div>
      {#if !group.tiles.length}
        <p class="empty">Drag tiles here, or use Add tile.</p>
      {/if}
    {:else}
      <div class="grid" bind:this={grid}>
        {#each visible as tile (tile.id)}
          <div class="cell" style={span(tile.size)}>
            <Tile {tile} {onopenassistant} />
          </div>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  .group {
    display: grid;
    gap: 0.75rem;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem 1rem;
  }
  /* Group names are set extra-wide: the cabinet's stencilled section labels. */
  h2,
  .title-input {
    margin: 0;
    font-size: var(--step-1);
    font-weight: 750;
    font-stretch: 125%;
    letter-spacing: 0.02em;
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 1rem;
    flex: 1;
  }
  h2::after {
    content: '';
    flex: 1;
    height: 2px;
    background: var(--line);
    border-radius: 1px;
  }
  .title-input {
    flex: 1;
    min-width: 10rem;
    padding: 0.2rem 0.5rem;
    border: 1px dashed var(--muted);
    border-radius: 4px;
    background: var(--drawer);
  }
  .group-tools {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(var(--cell), 1fr));
    grid-auto-rows: var(--cell);
    grid-auto-flow: dense;
    gap: var(--gap);
    min-height: var(--cell);
  }
  .cell {
    min-width: 0;
  }
  .slot {
    height: 100%;
    border: 2px dashed var(--tape);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--tape) 14%, transparent);
  }
  .empty {
    margin: -0.25rem 0 0;
    color: var(--muted);
    font-size: var(--step--1);
  }
</style>
