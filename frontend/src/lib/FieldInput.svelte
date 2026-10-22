<script>
  // One tile-editor input, chosen by the integration's field type.
  import { untrack } from 'svelte';
  import { api } from './api.js';

  let { field, value = $bindable(), integrationId, onpick } = $props();
  const id = $derived(`field-${integrationId}-${field.key}`);

  // Picker state: search the integration's options, but typing a value directly also works.
  // A multipicker keeps a list of values, shown as chips, and its box is only for searching.
  const multi = $derived(field.type === 'multipicker');
  const chosen = $derived(multi ? (Array.isArray(value) ? value : value ? [value] : []) : []);
  let labels = $state({}); // value -> label, learned from search results, for the chips
  let query = $state(untrack(() => (multi ? '' : (value ?? ''))));
  let results = $state([]);
  let searching = $state(false);
  let error = $state('');
  let open = $state(false);
  let timer;
  let latest = 0; // only the newest search may update the list; older replies can arrive later

  function search() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const ticket = ++latest;
      searching = true;
      try {
        const found = await api.options(integrationId, field.key, query);
        if (ticket !== latest) return;
        results = found;
        for (const option of found) labels[option.value] = option.label;
        error = found.length ? '' : 'No matches.';
      } catch (e) {
        if (ticket !== latest) return;
        results = [];
        error = e.message;
      } finally {
        if (ticket === latest) searching = false;
      }
    }, 250);
  }

  function typed(text) {
    query = text;
    if (!multi) value = text;
    open = true;
    search();
  }

  function pick(option) {
    open = false;
    if (!multi) {
      value = option.value;
      query = option.value;
      return onpick?.(option);
    }
    query = ''; // no onpick: one of several picks is a poor name for the whole tile
    if (!chosen.includes(option.value)) value = [...chosen, option.value];
  }

  const unpick = (v) => (value = chosen.filter((c) => c !== v));

  // Reorder chips: native drag and drop (it works inside the modal <dialog>, where the drag
  // library's floating copy would sit behind it), or the arrow keys on a focused chip.
  let dragging = $state(null);
  function move(from, to) {
    if (from === to || to < 0 || to >= chosen.length) return;
    const next = [...chosen];
    next.splice(to, 0, next.splice(from, 1)[0]);
    value = next;
  }
  function chipKey(event, i) {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!step) return;
    event.preventDefault();
    move(i, i + step);
    const chips = event.currentTarget.parentElement;
    queueMicrotask(() => chips.children[i + step]?.focus());
  }

  // Saved multipicker values are bare ids; one search fills in their labels for the chips.
  untrack(() => multi && chosen.length && search());
</script>

{#if field.type === 'bool'}
  <label class="check">
    <input type="checkbox" checked={value ?? field.default ?? false} onchange={(e) => (value = e.currentTarget.checked)} />
    <span>{field.label}</span>
  </label>
{:else}
  <div class="field">
    <span><label for={id}>{field.label}</label>{#if !field.required}<em> (optional)</em>{/if}</span>
    {#if field.type === 'select'}
      <select {id} class="input" bind:value>
        {#each field.options as option (option.value)}
          <option value={option.value}>{option.label}</option>
        {/each}
      </select>
    {:else if field.type === 'textarea'}
      <textarea {id} class="input" rows="4" placeholder={field.placeholder} bind:value></textarea>
    {:else if field.type === 'picker' || multi}
      <div class="picker">
        {#if chosen.length}
          <div class="chips">
            {#each chosen as v, i (v)}
              <div
                class="chip"
                draggable="true"
                tabindex="0"
                role="button"
                aria-roledescription="reorderable item"
                aria-label="{labels[v] ?? v}, {i + 1} of {chosen.length}. Arrow keys move it."
                title="Drag, or use the arrow keys, to reorder"
                class:dragging={dragging === i}
                ondragstart={(e) => ((dragging = i), (e.dataTransfer.effectAllowed = 'move'))}
                ondragover={(e) => e.preventDefault()}
                ondrop={(e) => (e.preventDefault(), move(dragging, i))}
                ondragend={() => (dragging = null)}
                onkeydown={(e) => chipKey(e, i)}
              >
                {labels[v] ?? v}
                <button type="button" onclick={() => unpick(v)} aria-label="Remove {labels[v] ?? v}">✕</button>
              </div>
            {/each}
          </div>
        {/if}
        <input
          {id}
          class="input"
          role="combobox"
          aria-expanded={open}
          aria-controls="{id}-list"
          autocomplete="off"
          placeholder={field.placeholder || (multi ? 'Type to add' : 'Type to search')}
          value={query}
          oninput={(e) => typed(e.currentTarget.value)}
          onfocus={() => {
            open = true;
            search();
          }}
          onkeydown={(e) => {
            // Close only the suggestions; preventDefault stops the dialog from closing too.
            if (e.key !== 'Escape' || !open) return;
            e.preventDefault();
            e.stopPropagation();
            open = false;
          }}
        />
        {#if open}
          <ul id="{id}-list" class="results" role="listbox">
            {#if searching && !results.length}
              <li class="note">Searching…</li>
            {:else if error}
              <li class="note">{error}</li>
            {/if}
            {#each results as option (option.value)}
              <li role="option" aria-selected={multi ? chosen.includes(option.value) : option.value === value}>
                <button type="button" onclick={() => pick(option)}>
                  {option.label}
                  {#if option.detail && option.detail !== option.label}<small>{option.detail}</small>{/if}
                </button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {:else}
      <input
        {id}
        class="input"
        type={field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : 'text'}
        placeholder={field.placeholder}
        required={field.required}
        bind:value
      />
    {/if}
    {#if field.help}<small>{field.help}</small>{/if}
  </div>
{/if}

<style>
  em {
    font-style: normal;
    font-weight: 400;
    color: var(--muted);
  }
  .check {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: var(--step--1);
    font-weight: 600;
  }
  .check input {
    width: 1.1rem;
    height: 1.1rem;
    accent-color: var(--ok);
  }
  .picker {
    position: relative;
  }
  .results {
    position: absolute;
    z-index: 5;
    left: 0;
    right: 0;
    top: calc(100% + 4px);
    max-height: 16rem;
    overflow: auto;
    margin: 0;
    padding: 0.25rem;
    list-style: none;
    background: var(--drawer);
    border: 1px solid var(--line);
    border-radius: 4px;
    box-shadow: 0 8px 24px #1d242226;
  }
  .results button {
    display: grid;
    width: 100%;
    padding: 0.4rem 0.5rem;
    border: 0;
    border-radius: 3px;
    background: none;
    text-align: left;
    cursor: pointer;
  }
  .results button:hover,
  [aria-selected='true'] button {
    background: var(--holder);
  }
  .results small {
    color: var(--muted);
    font-size: var(--step--1);
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
    margin: 0 0 0.4rem;
    padding: 0;
    list-style: none;
  }
  .chip {
    cursor: grab;
    display: flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.15rem 0.2rem 0.15rem 0.55rem;
    border-radius: 999px;
    background: var(--holder);
    font-size: var(--step--1);
    font-weight: 600;
  }
  .chip.dragging {
    opacity: 0.4;
  }
  .chip:focus-visible {
    outline: 3px solid var(--tape);
  }
  .chips button {
    width: 1.4rem;
    height: 1.4rem;
    border: 0;
    border-radius: 50%;
    background: none;
    color: var(--muted);
    cursor: pointer;
  }
  .chips button:hover {
    background: var(--line);
    color: inherit;
  }
  .note {
    padding: 0.4rem 0.5rem;
    color: var(--muted);
    font-size: var(--step--1);
  }
</style>
