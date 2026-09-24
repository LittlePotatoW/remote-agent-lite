<script lang="ts">
  import Icon from './Icon.svelte';
  import { taskWhenLabel } from '../lib/schedule';
  import type { Project, ScheduledTask, Session } from '../lib/types';

  export let project: Project | null = null;
  export let session: Session | null = null;
  export let tasks: ScheduledTask[] = [];
  export let loading = false;
  export let onClose: () => void;
  export let onNew: () => void;
  export let onTaskMenu: (task: ScheduledTask, event: MouseEvent) => void;
</script>

<header class="panel-header">
  <button class="icon-btn" type="button" aria-label="返回列表" on:click={onClose}>
    <Icon name="back" size={22} stroke={2} />
  </button>
  <div class="panel-title">
    <strong>定时任务</strong>
    <span>{project?.name ?? '项目'} / {session?.title ?? '对话'}</span>
  </div>
  <button class="icon-btn" type="button" aria-label="新建定时任务" on:click={onNew}>
    <Icon name="plus" size={22} />
  </button>
</header>

<div class="panel-body">
  {#if loading && tasks.length === 0}
    <div class="empty-block"><p>加载中…</p></div>
  {:else if tasks.length === 0}
    <div class="empty-block">
      <strong>还没有定时任务</strong>
      <button class="btn-primary" type="button" on:click={onNew}>新建定时任务</button>
    </div>
  {/if}

  {#each tasks as task (task.id)}
    <div class="task-row">
      <div class="task-main">
        <span class="row-title">{task.title}</span>
        <span class="task-when">
          {taskWhenLabel(task)}
          {#if task.image_count > 0}
            <span class="task-badge">{task.image_count} 张图</span>
          {/if}
        </span>
      </div>
      <div class="row-tail">
        {#if task.pinned}
          <span class="icon-btn" aria-label="已置顶"><Icon name="pin" size={16} /></span>
        {/if}
        <button
          class="icon-btn"
          type="button"
          aria-label={`${task.title} 的操作`}
          on:click={(event) => onTaskMenu(task, event)}
        >
          <Icon name="dots" size={20} />
        </button>
      </div>
    </div>
  {/each}
</div>
