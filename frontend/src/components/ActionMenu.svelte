<script lang="ts">
  import { onMount } from 'svelte';
  import Icon from './Icon.svelte';
  import type { MenuItem } from '../lib/types';

  export let anchor: { right: number; bottom: number; top: number } | null = null;
  export let items: MenuItem[] = [];
  export let onClose: () => void;

  const width = 176;
  const itemHeight = 48;

  let left = 8;
  let top = 8;

  onMount(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  $: if (anchor) {
    const height = items.length * itemHeight + 8;
    left = Math.min(Math.max(8, anchor.right - width + 4), window.innerWidth - width - 8);
    top =
      anchor.bottom + height + 12 > window.innerHeight
        ? Math.max(8, anchor.top - height - 8)
        : anchor.bottom + 8;
  }

  function choose(item: MenuItem) {
    onClose();
    item.onSelect();
  }
</script>

<div class="menu-scrim" on:click={onClose} role="presentation"></div>
<div class="menu" style="left:{left}px; top:{top}px; width:{width}px" role="menu">
  {#each items as item, index (item.label)}
    {#if index > 0 && item.danger}
      <div class="menu-sep"></div>
    {/if}
    <button
      type="button"
      role="menuitem"
      class:danger={item.danger}
      on:click={() => choose(item)}
    >
      {#if item.icon}<Icon name={item.icon} size={20} />{/if}
      {item.label}
    </button>
  {/each}
</div>
