<script lang="ts">
  import type { Project } from '../lib/types';
  import { formatTime } from '../lib/format';

  export let trash: Project[] = [];
  export let onRestore: (project: Project) => Promise<void>;
  export let onPurge: (project: Project) => Promise<void>;
  export let onEmpty: () => Promise<void>;
  export let onClose: () => void;
</script>

<div class="modal-backdrop" on:click={onClose} role="presentation">
  <div
    class="modal card"
    role="dialog"
    tabindex="0"
    on:click|stopPropagation
    on:keydown|stopPropagation
  >
    <div class="row">
      <h3 class="grow">回收站</h3>
      <button class="btn small danger" on:click={onEmpty} disabled={trash.length === 0}>清空</button>
      <button class="btn small ghost" on:click={onClose}>关闭</button>
    </div>
    {#if trash.length === 0}
      <div class="empty">回收站是空的</div>
    {/if}
    {#each trash as project (project.id)}
      <div class="file-row">
        <div class="grow">
          <div class="name">{project.name}</div>
          <div class="sub">删除于 {formatTime(project.trashed_at)} · 保留至 {formatTime(project.purge_at)}</div>
        </div>
        <button class="btn small" on:click={() => onRestore(project)}>恢复</button>
        <button class="btn small danger" on:click={() => onPurge(project)}>彻底删除</button>
      </div>
    {/each}
  </div>
</div>
