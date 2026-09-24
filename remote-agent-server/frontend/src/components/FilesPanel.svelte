<script lang="ts">
  import Icon from './Icon.svelte';
  import type { FileEntry, Project } from '../lib/types';
  import { formatBytes, formatTime } from '../lib/format';
  import { collectDropped, type DroppedFile } from '../lib/drop';

  export let project: Project | null = null;
  export let path = '';
  export let entries: FileEntry[] = [];
  export let loading = false;
  export let uploads: { name: string; sent: number; total: number }[] = [];
  export let onNavigate: (path: string) => void;
  export let onEntryMenu: (entry: FileEntry, event: MouseEvent) => void;
  export let onPick: (files: FileList) => void;
  export let onDropped: (items: DroppedFile[]) => void;
  export let onOpen: (entry: FileEntry) => void;
  export let onClose: () => void;

  let input: HTMLInputElement;
  let folderInput: HTMLInputElement;

  $: title = path ? path.split('/').filter(Boolean).pop()! : '文件';

  /** 电脑端按住 Shift 点「上传文件」= 选文件夹；移动端没有 Shift，行为不变。 */
  function pickFrom(event: MouseEvent) {
    if (event.shiftKey && folderInput) folderInput.click();
    else input?.click();
  }

  function handleDragOver(event: DragEvent) {
    if (!project || !event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
  }

  async function handleDrop(event: DragEvent) {
    if (!project) return;
    event.preventDefault();
    const items = await collectDropped(event.dataTransfer);
    if (items.length) onDropped(items);
  }
</script>

<header class="panel-header">
  <button class="icon-btn" type="button" aria-label="关闭面板" on:click={onClose}>
    <Icon name="close" size={20} />
  </button>
  <button
    class="icon-btn"
    type="button"
    aria-label="返回上一级"
    disabled={!path}
    on:click={() => onNavigate(path.split('/').slice(0, -1).join('/'))}
  >
    <Icon name="back" size={22} stroke={2} />
  </button>
  <div class="panel-title">
    <strong>{title}</strong>
    <span>{project ? `${project.name}${path ? ' · ' + path : ''}` : '未选择项目'}</span>
  </div>
</header>

<div
  class="panel-body"
  role="presentation"
  on:dragover={handleDragOver}
  on:drop={handleDrop}
>
  {#if !project}
    <div class="empty-block">
      <strong>还没有选择项目</strong>
      <p>先在左侧项目列表里打开一个项目</p>
    </div>
  {:else if loading && entries.length === 0}
    <div class="empty-block"><p>加载中…</p></div>
  {:else if entries.length === 0 && uploads.length === 0}
    <div class="empty-block">
      <strong>这里是空的</strong>
      <p>点下面的按钮上传文件</p>
    </div>
  {/if}

  {#each entries as entry (entry.path)}
    <div class="file-row">
      <button class="row-main" type="button" on:click={() => onOpen(entry)}>
        <span class="file-name">
          <Icon name={entry.type === 'directory' ? 'folder' : 'file'} size={20} />
          <span>{entry.name}</span>
        </span>
        <span class="file-meta">
          {#if entry.type === 'directory'}
            文件夹
          {:else}
            {formatBytes(entry.size)} · {formatTime(entry.mtime)}
          {/if}
        </span>
      </button>
      <button
        class="icon-btn"
        type="button"
        aria-label={`${entry.name} 的操作`}
        on:click={(event) => onEntryMenu(entry, event)}
      >
        <Icon name="dots" size={20} />
      </button>
    </div>
  {/each}

  {#each uploads as item (item.name)}
    <div class="upload-row">
      <div class="upload-head">
        <span>{item.name}</span>
        <span class="percent">{Math.round((item.sent / Math.max(1, item.total)) * 100)}%</span>
      </div>
      <div class="upload-track" role="progressbar" aria-valuenow={item.sent} aria-valuemax={item.total}>
        <div class="upload-fill" style="width:{Math.round((item.sent / Math.max(1, item.total)) * 100)}%"></div>
      </div>
    </div>
  {/each}
</div>

<div class="panel-footer">
  <button class="btn-primary" type="button" disabled={!project} on:click={pickFrom}>
    <Icon name="upload" size={20} />
    上传文件
  </button>
  <input
    bind:this={input}
    type="file"
    multiple
    class="sr-only"
    tabindex="-1"
    aria-hidden="true"
    on:change={(event) => {
      const files = (event.currentTarget as HTMLInputElement).files;
      if (files && files.length) onPick(files);
      if (input) input.value = '';
    }}
  />
  <input
    bind:this={folderInput}
    type="file"
    multiple
    webkitdirectory
    class="sr-only"
    tabindex="-1"
    aria-hidden="true"
    on:change={(event) => {
      const files = (event.currentTarget as HTMLInputElement).files;
      if (files && files.length) onPick(files);
      if (folderInput) folderInput.value = '';
    }}
  />
</div>
