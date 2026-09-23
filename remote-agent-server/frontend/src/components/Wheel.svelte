<script lang="ts">
  import { onMount } from 'svelte';
  import type { WheelOption } from '../lib/schedule';

  export let label = '';
  export let options: WheelOption[] = [];
  export let value: number;

  const ROW = 36;
  let view: HTMLDivElement;
  let settle: ReturnType<typeof setTimeout> | undefined;
  let dragging = false;

  function indexOf(target: number): number {
    const index = options.findIndex((option) => option.value === target);
    return index < 0 ? 0 : index;
  }

  function align(behavior: ScrollBehavior) {
    if (!view) return;
    const top = indexOf(value) * ROW;
    if (Math.abs(view.scrollTop - top) < 1) return;
    view.scrollTo({ top, behavior });
  }

  onMount(() => {
    align('auto');
    return () => clearTimeout(settle);
  });

  // 外部改了值（比如切换月份后「日」的范围变了）就把滚轮滚到新位置
  let last = Number.NaN;
  $: if (view && value !== last) {
    last = value;
    if (!dragging) align('smooth');
  }

  function onScroll() {
    if (!view) return;
    dragging = true;
    clearTimeout(settle);
    settle = setTimeout(() => {
      dragging = false;
      const picked = options[Math.round(view.scrollTop / ROW)];
      if (picked && picked.value !== value) value = picked.value;
      else align('smooth');
    }, 90);
  }

  function pick(option: WheelOption) {
    value = option.value;
    align('smooth');
  }
</script>

<div class="wheel">
  <span class="wheel-label">{label}</span>
  <div class="wheel-view" bind:this={view} on:scroll={onScroll}>
    <div class="wheel-pad" aria-hidden="true"></div>
    {#each options as option (option.value)}
      <button
        type="button"
        class="wheel-item"
        class:active={option.value === value}
        aria-label={`${label}${option.label}`}
        on:click={() => pick(option)}
      >{option.label}</button>
    {/each}
    <div class="wheel-pad" aria-hidden="true"></div>
  </div>
</div>
