<script lang="ts">
  import Icon from './Icon.svelte';
  import type { Project, Session } from '../lib/types';

  export let projects: Project[] = [];
  export let activeSessionId = '';
  export let onSelectSession: (project: Project, session: Session) => void;
  export let onNewProject: () => void;
  export let onNewSession: (project: Project) => void;
  export let onProjectMenu: (project: Project, event: MouseEvent) => void;
  export let onSessionMenu: (session: Session, event: MouseEvent) => void;
  export let onOpenSettings: () => void;
  export let onClose: () => void;

  let collapsed: Record<string, boolean> = {};

  function toggle(project: Project) {
    collapsed = { ...collapsed, [project.id]: !collapsed[project.id] };
  }
</script>

<header class="panel-header">
  <button class="icon-btn" type="button" aria-label="关闭面板" on:click={onClose}>
    <Icon name="close" size={20} />
  </button>
  <div class="panel-title"><strong>项目</strong></div>
  <button class="icon-btn" type="button" on:click={onNewProject} aria-label="新建项目">
    <Icon name="plus" size={22} />
  </button>
</header>

<div class="panel-body">
  {#if projects.length === 0}
    <div class="tree-empty">
      <p>还没有项目</p>
      <button class="btn-primary" type="button" on:click={onNewProject}>新建项目</button>
    </div>
  {:else}
    <div class="tree-list">
      {#each projects as project (project.id)}
        <div class="tree-row project">
          <button
            class="row-main"
            type="button"
            aria-expanded={!collapsed[project.id]}
            on:click={() => toggle(project)}
          >
            <Icon name="folder" size={20} />
            <span class="row-title">{project.name}</span>
          </button>
          <div class="row-tail">
            <button
              class="icon-btn"
              type="button"
              aria-label={`在 ${project.name} 下新建对话`}
              on:click={() => onNewSession(project)}
            >
              <Icon name="compose" size={20} />
            </button>
            <button
              class="icon-btn"
              type="button"
              aria-label={`${project.name} 的操作`}
              on:click={(event) => onProjectMenu(project, event)}
            >
              <Icon name="dots" size={20} />
            </button>
          </div>
        </div>
        {#if !collapsed[project.id]}
          {#each project.sessions as session (session.id)}
            <div class="tree-row session" class:selected={session.id === activeSessionId}>
              <button class="row-main" type="button" on:click={() => onSelectSession(project, session)}>
                <span class="row-title">{session.title}</span>
                {#if session.unread_count > 0 && session.id !== activeSessionId}
                  <span class="unread-dot" aria-label="有新的回复"></span>
                {/if}
              </button>
              <div class="row-tail">
                {#if session.job_status === 'running'}
                  <span class="spinner" aria-label="正在运行"></span>
                {/if}
                {#if session.pinned}
                  <span class="icon-btn" aria-label="已置顶"><Icon name="pin" size={16} /></span>
                {/if}
                {#if session.id === activeSessionId}
                  <button
                    class="icon-btn"
                    type="button"
                    aria-label={`${session.title} 的操作`}
                    on:click={(event) => onSessionMenu(session, event)}
                  >
                    <Icon name="dots" size={20} />
                  </button>
                {/if}
              </div>
            </div>
          {/each}
        {/if}
      {/each}
    </div>
  {/if}
</div>

<div class="panel-footer">
  <button class="account-row" type="button" on:click={onOpenSettings}>
    <span class="avatar"><Icon name="person" size={20} /></span>
    <span class="account-name">管理员</span>
    <Icon name="dots" size={20} />
  </button>
</div>
