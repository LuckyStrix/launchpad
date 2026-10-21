<script>
  // Add or edit a tile. The form is built from the integration's field schema.
  import { untrack } from 'svelte';
  import { board, change, findTile, newId } from './board.svelte.js';
  import FieldInput from './FieldInput.svelte';

  /** target: { groupId } to add a tile, or { tileId } to edit one. */
  let { target, onclose } = $props();

  const SIZES = [
    ['1x1', 'Small'],
    ['2x1', 'Wide'],
    ['1x2', 'Tall'],
    ['2x2', 'Large'],
  ];

  // The editor is opened fresh for each target, so reading it once is intended.
  const existing = untrack(() => (target.tileId ? findTile(target.tileId)?.tile : null));
  let draft = $state(
    existing
      ? structuredClone($state.snapshot(existing))
      : { integration: null, title: '', icon: '', size: '1x1', config: {} },
  );
  let dialog;

  const schema = $derived(draft.integration ? board.integrations[draft.integration] : null);
  const choices = $derived(
    Object.values(board.integrations).sort((a, b) => (a.id === 'link' ? -1 : a.name.localeCompare(b.name))),
  );
  const missing = $derived(
    (schema?.fields ?? []).filter((f) => f.required && (draft.config[f.key] ?? '') === '').map((f) => f.label),
  );

  $effect(() => {
    dialog.showModal();
  });

  function choose(integration) {
    draft.integration = integration.id;
    draft.size = integration.default_size;
    draft.config = Object.fromEntries(
      integration.fields.filter((f) => f.default !== null).map((f) => [f.key, f.default]),
    );
  }

  function picked(field, option) {
    // Name the tile after the first thing picked, unless the user already named it.
    if (!draft.title) draft.title = option.label;
  }

  function submit(event) {
    event.preventDefault();
    if (missing.length) return;
    const tile = $state.snapshot(draft);
    change((layout) => {
      if (existing) {
        const found = layout.groups.flatMap((g) => g.tiles).find((t) => t.id === existing.id);
        Object.assign(found, tile);
      } else {
        layout.groups.find((g) => g.id === target.groupId).tiles.push({ ...tile, id: newId() });
      }
    });
    dialog.close();
  }
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="editor-title">
  {#if !schema}
    <h2 id="editor-title">Add a tile</h2>
    <ul class="choices">
      {#each choices as integration (integration.id)}
        <li>
          <button class="choice" onclick={() => choose(integration)}>
            <strong>{integration.name}</strong>
            <span>{integration.description}</span>
            {#if !integration.configured}
              <small>Needs {integration.missing.join(', ')} in .env before it shows data.</small>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
    <div class="actions">
      <button class="btn" onclick={() => dialog.close()}>Cancel</button>
    </div>
  {:else}
    <form onsubmit={submit}>
      <h2 id="editor-title">{existing ? 'Tile settings' : `New ${schema.name} tile`}</h2>

      {#each schema.fields as field (field.key)}
        <FieldInput
          {field}
          integrationId={schema.id}
          bind:value={draft.config[field.key]}
          onpick={(option) => picked(field, option)}
        />
      {/each}

      <label class="field">
        <span>Name</span>
        <input class="input" bind:value={draft.title} placeholder={schema.name} />
      </label>

      <label class="field">
        <span>Icon <em>(optional)</em></span>
        <input class="input" bind:value={draft.icon} placeholder={schema.icon || 'Site favicon'} />
        <small>An emoji, an image URL, or si:name for a Simple Icons logo (like si:github).</small>
      </label>

      <fieldset class="field">
        <legend>Size</legend>
        <div class="sizes">
          {#each SIZES as [size, label] (size)}
            <label class:chosen={draft.size === size}>
              <input type="radio" name="size" value={size} bind:group={draft.size} />
              <span class="shape s{size}" aria-hidden="true"></span>
              {label}
            </label>
          {/each}
        </div>
      </fieldset>

      <div class="actions">
        {#if missing.length}<p class="missing">Fill in {missing.join(', ')}.</p>{/if}
        {#if !existing}
          <button type="button" class="btn quiet" onclick={() => (draft.integration = null)}>Back</button>
        {/if}
        <button type="button" class="btn" onclick={() => dialog.close()}>Cancel</button>
        <button type="submit" class="btn primary" disabled={missing.length > 0}>
          {existing ? 'Apply' : 'Add tile'}
        </button>
      </div>
    </form>
  {/if}
</dialog>

<style>
  dialog {
    width: min(34rem, calc(100vw - 2rem));
    max-height: calc(100vh - 2rem);
    padding: 1.25rem;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--drawer);
    color: var(--ink);
    box-shadow: 0 20px 60px #0005;
  }
  dialog::backdrop {
    background: #1d242266;
  }
  h2 {
    margin: 0 0 1rem;
    font-size: var(--step-1);
    font-stretch: 112%;
  }
  form {
    display: grid;
    gap: 1rem;
  }
  em {
    font-style: normal;
    font-weight: 400;
    color: var(--muted);
  }
  .choices {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 0.4rem;
  }
  .choice {
    display: grid;
    gap: 0.15rem;
    width: 100%;
    padding: 0.7rem 0.8rem;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--holder);
    text-align: left;
    cursor: pointer;
  }
  .choice:hover {
    border-color: var(--muted);
  }
  .choice span,
  .choice small {
    color: var(--muted);
    font-size: var(--step--1);
  }
  .choice small {
    color: var(--warn);
  }
  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
  }
  legend {
    margin-bottom: 0.3rem;
    font-size: var(--step--1);
    font-weight: 600;
  }
  .sizes {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .sizes label {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.4rem 0.7rem;
    border: 1px solid var(--line);
    border-radius: 999px;
    font-size: var(--step--1);
    cursor: pointer;
  }
  .sizes label.chosen {
    border-color: var(--tape);
    background: color-mix(in srgb, var(--tape) 18%, transparent);
  }
  .sizes label:has(:focus-visible) {
    outline: 3px solid var(--tape);
  }
  .sizes input {
    position: absolute;
    opacity: 0;
  }
  .shape {
    display: inline-block;
    border: 1.5px solid currentColor;
    border-radius: 2px;
    width: 0.6rem;
    height: 0.6rem;
  }
  .s2x1 {
    width: 1.2rem;
  }
  .s1x2 {
    height: 1.2rem;
  }
  .s2x2 {
    width: 1.2rem;
    height: 1.2rem;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }
  .missing {
    margin: 0 auto 0 0;
    color: var(--muted);
    font-size: var(--step--1);
  }
</style>
