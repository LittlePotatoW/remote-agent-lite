<script lang="ts">
  import type { FileEntry, Project } from '../lib/types';
  import { formatBytes, formatTime } from '../lib/format';

  export let project: Project | null = null;
  export let entries: FileEntry[] = [];
  export let path = '';
  export let loading = false;
  export let progress = new Map<string, { sent: number; total: number }>();
  export let mobileVisible = false;
  export let onNavigate: (path: string) => void;
  export let onUpload: (files: FileList) => Promise<void>;
  export let onDelete: (entry: FileEntry) => Promise<void>;
  export let onRefresh: () => Promise<void>;

  let input: HTMLInputElement;

  function crumbs() {
    const parts = path.split('/').filter(Boolean);
    return parts.map((part, index) => ({
      name: part,
      path: parts.slice(0, index + 1).join('/')
    }));
  }

  async function fileSelected(event: Event) {
    const files = (event.target as HTMLInputElement).files;
    if (files && files.length) await onUpload(files);
    if (input) input.value = '';
  }
</script>

<div class="side-panel" class:mobile-visible={mobileVisible}>
  <div class="panel-head">
    <strong>文件</strong>
    <div class="row">
      <button class="btn small ghost" on:click={onRefresh} disabled={loading}>刷新</button>
      <button class="btn small" on:click={() => input?.click()} disabled={!project}>上传</button>
      <input
        bind:this={input}
        type="file"
        multiple
        style="display:none"
        on:change={fileSelected}
      />
    </div>
  </div>
  <div class="panel-scroll">
    {#if !project}
      <div class="empty">选择一个项目查看文件</div>
    {:else}
      <div class="breadcrumbs">
        <button on:click={() => onNavigate('')}>根目录</button>
        {#each crumbs() as crumb}
          <span>/</span>
          <button on:click={() => onNavigate(crumb.path)}>{crumb.name}</button>
        {/each}
      </div>
      {#if loading}
        <div class="empty">加载中…</div>
      {:else if entries.length === 0}
        <div class="empty">这个目录是空的</div>
      {/if}
      {#each entries as entry (entry.path)}
        <div class="file-row">
          <div class="grow">
            {#if entry.type === 'directory'}
              <button class="name" style="background:none;border:0;text-align:left" on:click={() => onNavigate(entry.path)}>
                📁 {entry.name}
              </button>
            {:else}
              <div class="name">📄 {entry.name}</div>
            {/if}
            <div class="sub">
              {#if entry.type === 'file'}{formatBytes(entry.size)} · {/if}{formatTime(entry.mtime)}
              {#if progress.get(entry.name)}
                · 上传中 {Math.round(((progress.get(entry.name)!.sent / Math.max(1, progress.get(entry.name)!.total)) * 100))}%
              {/if}
            </div>
          </div>
          <div class="file-actions">
            {#if entry.type === 'file'}
              <a class="btn small ghost" href={`/api/projects/${project.id}/files/download?path=${encodeURIComponent(entry.path)}`}>下载</a>
              <button class="btn small ghost danger" on:click={() => onDelete(entry)}>删</button>
            {/if}
          </div>
        </div>
      {/each}
      {#if progress.size > 0}
        <div class="muted" style="margin-top:12px">
          {#each Array.from(progress.entries()) as [name, item]}
            <div>{name}: {formatBytes(item.sent)} / {formatBytes(item.total)}</div>
          {/each}
        </div>
      {/if}
    {/if}
  </div>
</div>
