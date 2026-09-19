<script lang="ts">
  import type { GitCommit, Project } from '../lib/types';
  import { formatTime } from '../lib/format';

  export let project: Project | null = null;
  export let commits: GitCommit[] = [];
  export let selected: { commit: string; stat: string; files: Array<Record<string, string>> } | null = null;
  export let loading = false;
  export let mobileVisible = false;
  export let onSelect: (commit: GitCommit) => Promise<void>;
  export let onRestore: (commit: GitCommit) => Promise<void>;
  export let onRefresh: () => Promise<void>;
</script>

<div class="side-panel" class:mobile-visible={mobileVisible}>
  <div class="panel-head">
    <strong>版本</strong>
    <button class="btn small ghost" on:click={onRefresh} disabled={!project || loading}>刷新</button>
  </div>
  <div class="panel-scroll">
    {#if !project}
      <div class="empty">选择一个项目查看版本</div>
    {:else if loading}
      <div class="empty">加载中…</div>
    {:else if commits.length === 0}
      <div class="empty">还没有快照。完成一轮对话后会自动生成。</div>
    {/if}
    {#each commits as commit (commit.hash)}
      <div class="file-row">
        <div class="grow">
          <div class="name">{commit.subject}</div>
          <div class="sub mono">{commit.hash.slice(0, 10)} · {formatTime(commit.timestamp)}</div>
        </div>
        <div class="file-actions">
          <button class="btn small ghost" on:click={() => onSelect(commit)}>查看</button>
          <button class="btn small ghost danger" on:click={() => onRestore(commit)}>恢复</button>
        </div>
      </div>
    {/each}
    {#if selected}
      <div style="margin-top:16px">
        <div class="section-title"><span>变更</span></div>
        <pre class="stat">{selected.stat}</pre>
      </div>
    {/if}
  </div>
</div>
