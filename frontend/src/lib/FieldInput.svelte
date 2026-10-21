<script>
  // One tile-editor input, chosen by the integration's field type.
  import { api } from './api.js';

  let { field, value = $bindable(), integrationId, onpick } = $props();
  const id = $derived(`field-${integrationId}-${field.key}`);

  // Picker state: search the integration's options, but typing a value directly also works.
  let query = $state(value ?? '');
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
    value = text;
    open = true;
    search();
  }

  function pick(option) {
    value = option.value;
    query = option.value;
    open = false;
    onpick?.(option);
  }
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
    {:else if field.type === 'picker'}
      <div class="picker">
        <input
          {id}
          class="input"
          role="combobox"
          aria-expanded={open}
          aria-controls="{id}-list"
          autocomplete="off"
          placeholder={field.placeholder || 'Type to search'}
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
              <li role="option" aria-selected={option.value === value}>
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
  .note {
    padding: 0.4rem 0.5rem;
    color: var(--muted);
    font-size: var(--step--1);
  }
</style>
