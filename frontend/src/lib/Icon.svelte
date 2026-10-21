<script>
  // A tile icon: an emoji or glyph, an image URL, "si:<slug>" for a Simple Icons logo,
  // or (with only a `url`) that site's favicon. Falls back to the label's first letter.
  let { icon = '', fallback = '', url = '', label = '' } = $props();
  let failed = $state(false);

  function resolve(value) {
    if (value.startsWith('si:')) return { img: `https://cdn.simpleicons.org/${value.slice(3)}/1d2422/e6eae5` };
    if (/^(https?:)?\/\//.test(value) || value.startsWith('/')) return { img: value };
    return { text: value };
  }

  const resolved = $derived.by(() => {
    if (icon) return resolve(icon);
    if (/^https?:/.test(url)) {
      try {
        return { img: new URL('/favicon.ico', url).href, alt: resolve(fallback) };
      } catch {}
    }
    return resolve(fallback);
  });

  $effect(() => {
    resolved;
    failed = false;
  });

  const shown = $derived(failed ? (resolved.alt ?? { text: '' }) : resolved);
</script>

<span class="icon" aria-hidden="true">
  {#if shown.img}
    <img src={shown.img} alt="" loading="lazy" onerror={() => (failed = true)} />
  {:else}
    {shown.text || label.trim().charAt(0).toUpperCase()}
  {/if}
</span>

<style>
  .icon {
    display: inline-grid;
    place-items: center;
    flex: none;
    width: 1.35rem;
    height: 1.35rem;
    font-size: 1rem;
    line-height: 1;
    font-weight: 700;
    font-stretch: 85%;
  }
  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
  }
</style>
