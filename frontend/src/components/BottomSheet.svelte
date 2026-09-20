<script lang="ts">
  import { onMount } from 'svelte';

  export let title = '';
  export let onClose: () => void;

  onMount(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });
</script>

<div class="sheet-scrim" on:click={onClose} role="presentation"></div>
<div class="sheet" role="dialog" aria-modal="true" aria-label={title || '操作'}>
  <div class="sheet-handle"></div>
  {#if title}<h2>{title}</h2>{/if}
  <slot />
</div>
