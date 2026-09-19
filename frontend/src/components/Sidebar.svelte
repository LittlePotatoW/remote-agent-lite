<script lang="ts">
  import type { Project, Session } from '../lib/types';
  import { formatTime, shortTitle } from '../lib/format';

  export let projects: Project[] = [];
  export let activeProjectId = '';
  export let sessions: Session[] = [];
  export let activeSessionId = '';
  export let onSelectProject: (id: string) => void;
  export let onCreateProject: (name: string) => Promise<void>;
  export let onSelectSession: (id: string) => void;
  export let onCreateSession: () => Promise<void>;
  export let onDeleteSession: (session: Session) => void;
  export let onOpenTrash: () => void;
  export let onOpenSettings: () => void;
  export let mobileVisible = false;

  let newName = '';
  let creating = false;

  async function createProject() {
    const name = newName.trim();
    if (!name || creating) return;
    creating = true;
    try {
      await onCreateProject(name);
      newName = '';
    } finally {
      creating = false;
    }
  }
</script>

<aside class="sidebar" class:mobile-visible={mobileVisible}>
  <div class="brand">
    <div>
      <strong>remote-agent-lite</strong><br />
      <small>常驻 Codex</small>
    </div>
    <button class="btn small ghost" on:click={onOpenSettings} title="设置">⚙</button>
  </div>
  <div class="sidebar-scroll">
    <div class="section-title">
      <span>项目</span>
      <span>{projects.length}</span>
    </div>
    <div class="list">
      {#each projects as project (project.id)}
        <button
          class="list-item"
          class:active={project.id === activeProjectId}
          on:click={() => onSelectProject(project.id)}
        >
          <span class="grow">
            <span class="title">{project.name}</span>
            <span class="sub">{project.session_count || 0} 个会话 · {project.size_mb || 0} MB</span>
          </span>
        </button>
      {/each}
      {#if projects.length === 0}
        <div class="empty">还没有项目</div>
      {/if}
    </div>

    <form class="row" style="margin-top: 10px" on:submit|preventDefault={createProject}>
      <input class="grow" placeholder="新项目名称" bind:value={newName} disabled={creating} />
      <button class="btn small" type="submit" disabled={creating || !newName.trim()}>+</button>
    </form>

    {#if activeProjectId}
      <div class="section-title">
        <span>会话</span>
        <button class="btn small ghost" on:click={onCreateSession}>+</button>
      </div>
      <div class="list">
        {#each sessions as session (session.id)}
          <div class="list-item" class:active={session.id === activeSessionId}>
            <button class="grow" style="background:none;border:0;text-align:left;min-width:0" on:click={() => onSelectSession(session.id)}>
              <div class="title">{session.title}</div>
              <div class="sub">
                {formatTime(session.updated_at)}
                {#if session.unread_count}· {session.unread_count} 条未读{/if}
              </div>
            </button>
            <button class="btn small ghost danger" title="删除会话" on:click={() => onDeleteSession(session)}>×</button>
          </div>
        {/each}
        {#if sessions.length === 0}
          <div class="empty">还没有会话</div>
        {/if}
      </div>
    {/if}
  </div>
  <div class="panel-head" style="border-top:1px solid var(--border);border-bottom:0">
    <button class="btn small ghost" on:click={onOpenTrash}>回收站</button>
    <span class="muted" style="font-size:11px">{projects.length} 个项目</span>
  </div>
</aside>
