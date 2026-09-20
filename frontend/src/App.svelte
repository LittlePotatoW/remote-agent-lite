<script lang="ts">
  import { onMount } from 'svelte';
  import ActionMenu from './components/ActionMenu.svelte';
  import BottomSheet from './components/BottomSheet.svelte';
  import ChatView from './components/ChatView.svelte';
  import FilesPanel from './components/FilesPanel.svelte';
  import Login from './components/Login.svelte';
  import SettingsView from './components/SettingsView.svelte';
  import TreePanel from './components/TreePanel.svelte';
  import { ApiError, client, subscribeEvents, uploadFile } from './lib/api';
  import { applyTheme, readTheme } from './lib/theme';
  import { isImagePath, rawImageUrl } from './lib/media';
  import type {
    FileEntry,
    MenuItem,
    Message,
    Project,
    ServerInfo,
    Session,
    ThemeName
  } from './lib/types';

  type Panel = 'tree' | 'files' | null;
  type Sheet =
    | { kind: 'newProject' }
    | { kind: 'rename'; scope: 'project' | 'session'; id: string; value: string }
    | { kind: 'deleteProject'; project: Project }
    | { kind: 'deleteSession'; session: Session }
    | { kind: 'deleteEntry'; entry: FileEntry }
    | { kind: 'password' };

  let booting = true;
  let authed = false;
  let setupRequired = false;
  let loginError = '';
  let loginBusy = false;

  let theme: ThemeName = readTheme();

  let projects: Project[] = [];
  let activeProjectId = '';
  let activeSessionId = '';
  let messages: Message[] = [];
  let running = false;
  let queued = 0;
  let chatError = '';
  let serverInfo: ServerInfo | null = null;
  let showSettings = false;

  let panel: Panel = null;
  let pull = 0;
  let dragging = false;
  let side: 'tree' | 'files' = 'tree';

  let filePath = '';
  let entries: FileEntry[] = [];
  let fileLoading = false;
  let uploads: { name: string; sent: number; total: number }[] = [];

  let sheet: Sheet | null = null;
  let sheetBusy = false;
  let sheetError = '';
  let menu: { anchor: { right: number; bottom: number; top: number }; items: MenuItem[] } | null =
    null;
  let toast = { text: '', error: false };
  let lightbox: { src: string; alt: string } | null = null;

  let cleanupEvents: (() => void) | null = null;
  let overviewTimer: number | null = null;
  let toastTimer: number | null = null;
  let eventSessionId = '';
  let dragEndedAt = 0;

  let startX = 0;
  let startY = 0;
  let axis: 'idle' | 'pending' | 'horizontal' | 'vertical' = 'idle';
  let base = 0;

  $: activeProject = projects.find((item) => item.id === activeProjectId) ?? null;
  $: activeSession = activeProject?.sessions.find((item) => item.id === activeSessionId) ?? null;
  $: if (authed && activeSessionId && activeSessionId !== eventSessionId) {
    eventSessionId = activeSessionId;
    bindEvents(activeSessionId);
  }

  function messageOf(value: unknown): string {
    if (value instanceof ApiError) return value.message;
    if (value instanceof Error) return value.message;
    return String(value);
  }

  function notify(text: string, error = false) {
    toast = { text, error };
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toast = { text: '', error: false };
    }, 3200);
  }

  onMount(async () => {
    applyTheme(theme);
    try {
      const status = await client.authStatus();
      setupRequired = status.setup_required;
      authed = status.authenticated;
      if (authed) await enterApp();
    } catch (error) {
      loginError = messageOf(error);
    } finally {
      booting = false;
    }
  });

  /* ---------- 启动与数据 ---------- */

  async function enterApp() {
    await Promise.all([refreshOverview(), refreshServerInfo()]);
    const stored = localStorage.getItem('ral-project');
    const project = projects.find((item) => item.id === stored) ?? projects[0] ?? null;
    if (project) await selectProject(project);
  }

  async function refreshOverview() {
    const result = await client.overview();
    projects = result.projects;
    if (activeProjectId && !projects.some((item) => item.id === activeProjectId)) {
      activeProjectId = '';
      activeSessionId = '';
      messages = [];
      entries = [];
      filePath = '';
    }
    const project = projects.find((item) => item.id === activeProjectId);
    if (project && activeSessionId && !project.sessions.some((item) => item.id === activeSessionId)) {
      activeSessionId = project.sessions[0]?.id ?? '';
    }
  }

  async function refreshServerInfo() {
    try {
      serverInfo = await client.serverInfo();
    } catch {
      serverInfo = null;
    }
  }

  function scheduleOverview() {
    if (overviewTimer) window.clearTimeout(overviewTimer);
    overviewTimer = window.setTimeout(() => {
      void refreshOverview();
    }, 400);
  }

  async function selectProject(project: Project) {
    activeProjectId = project.id;
    localStorage.setItem('ral-project', project.id);
    let target = project;
    if (target.sessions.length === 0) {
      await client.createSession(project.id);
      await refreshOverview();
      target = projects.find((item) => item.id === project.id) ?? project;
    }
    const session = target.sessions[0];
    if (session) await selectSession(session);
    closePanel();
  }

  async function selectSession(session: Session) {
    if (session.project_id && session.project_id !== activeProjectId) {
      activeProjectId = session.project_id;
      localStorage.setItem('ral-project', session.project_id);
    }
    activeSessionId = session.id;
    chatError = '';
    closePanel();
    await Promise.all([loadMessages(), refreshStatus(), loadFiles('')]);
  }

  async function loadMessages() {
    if (!activeSessionId) return;
    const result = await client.messages(activeSessionId);
    messages = result.messages;
  }

  async function refreshStatus() {
    if (!activeSessionId) {
      running = false;
      queued = 0;
      return;
    }
    try {
      const status = await client.sessionStatus(activeSessionId);
      running = Boolean(status.running_job_id);
      queued = status.queued_jobs;
    } catch {
      running = false;
      queued = 0;
    }
  }

  function bindEvents(sessionId: string) {
    cleanupEvents?.();
    cleanupEvents = subscribeEvents(sessionId, {
      'message.delta': (payload) => applyDelta(payload),
      'message.completed': () => {
        void loadMessages();
        void refreshStatus();
        scheduleOverview();
      },
      'turn.status': () => {
        void refreshStatus();
        scheduleOverview();
      },
      'session.status': () => scheduleOverview(),
      'files.changed': (payload) => {
        if (payload.project_id === activeProjectId) {
          void loadFiles(filePath);
          scheduleOverview();
        }
      }
    });
  }

  function applyDelta(payload: Record<string, unknown>) {
    const messageId = String(payload.message_id || '');
    const delta = String(payload.delta || '');
    if (!messageId || !delta) return;
    const index = messages.findIndex((message) => message.id === messageId);
    if (index >= 0) {
      const next = [...messages];
      next[index] = { ...next[index], content: next[index].content + delta, status: 'streaming' };
      messages = next;
    } else {
      messages = [
        ...messages,
        {
          id: messageId,
          session_id: activeSessionId,
          seq: Number(payload.seq || 0),
          role: 'assistant',
          content: delta,
          status: 'streaming',
          created_at: new Date().toISOString()
        }
      ];
    }
  }

  /* ---------- 对话 ---------- */

  async function sendMessage(prompt: string) {
    if (!activeSessionId) return;
    chatError = '';
    try {
      await client.send(activeSessionId, prompt);
      await Promise.all([loadMessages(), refreshStatus()]);
      scheduleOverview();
    } catch (error) {
      chatError = messageOf(error);
      throw error;
    }
  }

  function stopTurn() {
    if (!activeSessionId) return;
    void client.interrupt(activeSessionId).then(refreshStatus).catch(() => undefined);
  }

  async function createSessionIn(project: Project) {
    try {
      const created = await client.createSession(project.id);
      activeProjectId = project.id;
      localStorage.setItem('ral-project', project.id);
      await refreshOverview();
      await selectSession(created.session);
      notify('已新建对话');
    } catch (error) {
      notify(messageOf(error), true);
    }
  }

  /* ---------- 文件 ---------- */

  async function loadFiles(path: string) {
    if (!activeProjectId) {
      entries = [];
      filePath = '';
      return;
    }
    fileLoading = true;
    try {
      const result = await client.files(activeProjectId, path);
      filePath = result.path;
      entries = result.entries;
    } catch (error) {
      notify(messageOf(error), true);
    } finally {
      fileLoading = false;
    }
  }

  function openEntry(entry: FileEntry) {
    if (!activeProjectId) return;
    if (entry.type === 'directory') {
      void loadFiles(entry.path);
      return;
    }
    if (isImagePath(entry.name)) {
      lightbox = { src: rawImageUrl(activeProjectId, entry.path), alt: entry.name };
      return;
    }
    window.location.href = client.downloadUrl(activeProjectId, entry.path);
  }

  async function pickFiles(files: FileList) {
    if (!activeProjectId) return;
    const projectId = activeProjectId;
    const list = Array.from(files);
    uploads = [...uploads, ...list.map((file) => ({ name: file.name, sent: 0, total: file.size }))];
    for (const file of list) {
      try {
        await uploadFile(projectId, file, (sent, total) => {
          uploads = uploads.map((item) =>
            item.name === file.name ? { name: item.name, sent, total } : item
          );
        });
      } catch (error) {
        notify(`${file.name} 上传失败：${messageOf(error)}`, true);
      } finally {
        uploads = uploads.filter((item) => item.name !== file.name);
      }
    }
    await Promise.all([loadFiles(filePath), refreshOverview()]);
  }

  /* ---------- 手势与面板 ---------- */

  function panelWidth() {
    return Math.min(340, window.innerWidth * 0.86);
  }

  function openPanel(target: 'tree' | 'files') {
    side = target;
    panel = target;
    pull = panelWidth();
  }

  function closePanel() {
    panel = null;
    pull = 0;
  }

  function onPointerDown(event: PointerEvent) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const target = event.target as HTMLElement;
    if (target.closest('button, a, input, textarea, .sheet, .menu, .settings-page')) return;
    startX = event.clientX;
    startY = event.clientY;
    axis = 'pending';
    base = panel ? panelWidth() : 0;
    side = panel ?? 'tree';
  }

  function onPointerMove(event: PointerEvent) {
    if (axis === 'idle') return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (axis === 'pending') {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      if (Math.abs(dx) < Math.abs(dy) * 1.2) {
        axis = 'vertical';
        return;
      }
      axis = 'horizontal';
      dragging = true;
      if (!panel) side = dx > 0 ? 'tree' : 'files';
    }
    if (axis !== 'horizontal') return;
    const width = panelWidth();
    let next: number;
    if (panel === 'tree') next = base + dx;
    else if (panel === 'files') next = base - dx;
    else next = Math.abs(dx);
    pull = Math.max(0, Math.min(width, next));
  }

  function onPointerUp() {
    if (axis === 'horizontal') {
      const width = panelWidth();
      if (pull > width * 0.4) {
        panel = side;
        pull = width;
      } else {
        panel = null;
        pull = 0;
      }
      dragEndedAt = Date.now();
    }
    axis = 'idle';
    dragging = false;
  }

  function onScrimClick() {
    if (Date.now() - dragEndedAt < 300) return;
    closePanel();
  }

  function onKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (target?.closest('input, textarea, [contenteditable="true"]')) return;
    if (event.key === 'Escape') {
      if (lightbox) lightbox = null;
      else if (menu) menu = null;
      else if (sheet) sheet = null;
      else if (showSettings) showSettings = false;
      else if (panel) closePanel();
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      if (panel === 'files') closePanel();
      else openPanel('tree');
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      if (panel === 'tree') closePanel();
      else openPanel('files');
    }
  }

  /* ---------- 菜单与弹层 ---------- */

  function anchorOf(event: MouseEvent) {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return { right: rect.right, bottom: rect.bottom, top: rect.top };
  }

  function openProjectMenu(project: Project, event: MouseEvent) {
    menu = {
      anchor: anchorOf(event),
      items: [
        {
          label: '重命名',
          icon: 'pencil',
          onSelect: () => (sheet = { kind: 'rename', scope: 'project', id: project.id, value: project.name })
        },
        {
          label: project.pinned ? '取消置顶' : '置顶',
          icon: 'pin',
          onSelect: () => void setPinned('project', project.id, !project.pinned)
        },
        {
          label: '删除',
          icon: 'trash',
          danger: true,
          onSelect: () => (sheet = { kind: 'deleteProject', project })
        }
      ]
    };
  }

  function openSessionMenu(session: Session, event: MouseEvent) {
    menu = {
      anchor: anchorOf(event),
      items: [
        {
          label: '重命名',
          icon: 'pencil',
          onSelect: () => (sheet = { kind: 'rename', scope: 'session', id: session.id, value: session.title })
        },
        {
          label: session.pinned ? '取消置顶' : '置顶',
          icon: 'pin',
          onSelect: () => void setPinned('session', session.id, !session.pinned)
        },
        {
          label: '删除',
          icon: 'trash',
          danger: true,
          onSelect: () => (sheet = { kind: 'deleteSession', session })
        }
      ]
    };
  }

  function openEntryMenu(entry: FileEntry, event: MouseEvent) {
    const items: MenuItem[] = [];
    if (entry.type === 'file') {
      items.push({
        label: '下载',
        icon: 'download',
        onSelect: () => {
          if (activeProjectId) window.location.href = client.downloadUrl(activeProjectId, entry.path);
        }
      });
    }
    items.push({
      label: '删除',
      icon: 'trash',
      danger: true,
      onSelect: () => (sheet = { kind: 'deleteEntry', entry })
    });
    menu = { anchor: anchorOf(event), items };
  }

  async function setPinned(scope: 'project' | 'session', id: string, pinned: boolean) {
    try {
      if (scope === 'project') await client.updateProject(id, { pinned });
      else await client.updateSession(id, { pinned });
      await refreshOverview();
    } catch (error) {
      notify(messageOf(error), true);
    }
  }

  async function submitRename(value: string) {
    if (!sheet || sheet.kind !== 'rename') return;
    const next = value.trim();
    if (!next) return;
    sheetBusy = true;
    sheetError = '';
    try {
      if (sheet.scope === 'project') {
        await client.updateProject(sheet.id, { name: next });
        await refreshOverview();
      } else {
        await client.updateSession(sheet.id, { title: next });
        await Promise.all([refreshOverview(), loadMessages()]);
      }
      sheet = null;
    } catch (error) {
      sheetError = messageOf(error);
    } finally {
      sheetBusy = false;
    }
  }

  async function confirmDelete() {
    if (!sheet) return;
    sheetBusy = true;
    sheetError = '';
    try {
      if (sheet.kind === 'deleteProject') {
        await client.deleteProject(sheet.project.id);
        if (activeProjectId === sheet.project.id) {
          activeProjectId = '';
          activeSessionId = '';
          messages = [];
          entries = [];
          filePath = '';
        }
        await refreshOverview();
        const next = projects[0];
        if (next) await selectProject(next);
        notify('项目已删除');
      } else if (sheet.kind === 'deleteSession') {
        const wasActive = activeSessionId === sheet.session.id;
        await client.deleteSession(sheet.session.id);
        await refreshOverview();
        if (wasActive) {
          const project = projects.find((item) => item.id === activeProjectId);
          const next = project?.sessions[0];
          if (next) await selectSession(next);
          else {
            activeSessionId = '';
            messages = [];
          }
        }
      } else if (sheet.kind === 'deleteEntry') {
        if (!activeProjectId) return;
        await client.deleteEntry(activeProjectId, sheet.entry.path);
        await loadFiles(filePath);
      }
      sheet = null;
    } catch (error) {
      sheetError = messageOf(error);
    } finally {
      sheetBusy = false;
    }
  }

  async function submitNewProject(name: string) {
    const value = name.trim();
    if (!value) return;
    sheetBusy = true;
    sheetError = '';
    try {
      const created = await client.createProject(value);
      sheet = null;
      await refreshOverview();
      const project = projects.find((item) => item.id === created.project.id);
      if (project) await selectProject(project);
      notify('项目已创建');
    } catch (error) {
      sheetError = messageOf(error);
    } finally {
      sheetBusy = false;
    }
  }

  async function submitPassword(current: string, next: string) {
    sheetBusy = true;
    sheetError = '';
    try {
      await client.changePassword(current, next);
      sheet = null;
      notify('密码已更新');
    } catch (error) {
      sheetError = messageOf(error);
    } finally {
      sheetBusy = false;
    }
  }

  function setTheme(name: ThemeName) {
    theme = name;
    applyTheme(name);
  }

  async function submitLogin(password: string) {
    loginBusy = true;
    loginError = '';
    try {
      if (setupRequired) await client.setup(password);
      else await client.login(password);
      authed = true;
      await enterApp();
    } catch (error) {
      loginError = messageOf(error);
    } finally {
      loginBusy = false;
    }
  }

  let newProjectName = '';
  let renameValue = '';
  let passwordCurrent = '';
  let passwordNext = '';
  let passwordConfirm = '';

  function focusOnMount(node: HTMLElement) {
    node.focus();
  }

  $: if (sheet?.kind === 'rename') renameValue = sheet.value;
</script>

<svelte:window on:keydown={onKeydown} />

{#if booting}
  <div class="login-page"><p>加载中…</p></div>
{:else if !authed}
  <Login {setupRequired} error={loginError} busy={loginBusy} onSubmit={submitLogin} />
{:else}
  <div class="shell">
    <div
      class="stage"
      role="presentation"
      on:pointerdown={onPointerDown}
      on:pointermove={onPointerMove}
      on:pointerup={onPointerUp}
      on:pointercancel={onPointerUp}
    >
      <aside class="panel tree" style="--pull:{panel === 'tree' ? pull : 0}px">
        <TreePanel
          {projects}
          {activeSessionId}
          onSelectSession={(_, session) => void selectSession(session)}
          onNewProject={() => {
            newProjectName = '';
            sheetError = '';
            sheet = { kind: 'newProject' };
          }}
          onNewSession={(project) => void createSessionIn(project)}
          onProjectMenu={openProjectMenu}
          onSessionMenu={openSessionMenu}
          onOpenSettings={() => {
            showSettings = true;
            void refreshServerInfo();
          }}
        />
      </aside>

      <aside class="panel files" style="--pull:{panel === 'files' ? pull : 0}px">
        <FilesPanel
          project={activeProject}
          path={filePath}
          {entries}
          loading={fileLoading}
          {uploads}
          onNavigate={(path) => void loadFiles(path)}
          onEntryMenu={openEntryMenu}
          onPick={(files) => void pickFiles(files)}
          onOpen={openEntry}
        />
      </aside>

      <main
        class="page"
        class:dragging
        style="--shift:{panel === 'tree' ? pull : panel === 'files' ? -pull : 0}px; --page-radius:{panel ? 20 : 0}px; --page-shadow:{panel ? 'var(--shadow-panel)' : 'none'}"
      >
        <ChatView
          project={activeProject}
          session={activeSession}
          {messages}
          {running}
          {queued}
          errorText={chatError}
          onSend={sendMessage}
          onStop={stopTurn}
          onImage={(src, alt) => (lightbox = { src, alt })}
          onNewSession={() => {
            if (activeProject) void createSessionIn(activeProject);
          }}
        />
      </main>

      <div class="scrim" class:visible={panel !== null} on:click={onScrimClick} role="presentation"></div>
    </div>

    {#if showSettings}
      <SettingsView
        info={serverInfo}
        {theme}
        onTheme={setTheme}
        onChangePassword={() => {
          passwordCurrent = '';
          passwordNext = '';
          passwordConfirm = '';
          sheetError = '';
          sheet = { kind: 'password' };
        }}
        onClose={() => (showSettings = false)}
      />
    {/if}
  </div>

  {#if menu}
    <ActionMenu anchor={menu.anchor} items={menu.items} onClose={() => (menu = null)} />
  {/if}

  {#if sheet?.kind === 'newProject'}
    <BottomSheet title="新建项目" onClose={() => (sheet = null)}>
      <form
        on:submit|preventDefault={() => void submitNewProject(newProjectName)}
      >
        <label class="field">
          <span>项目名</span>
          <input
            bind:value={newProjectName}
            maxlength="80"
            placeholder="例如：数据分析脚本"
            autocomplete="off"
          />
        </label>
        {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
        <div class="sheet-actions">
          <button class="sheet-btn" type="button" on:click={() => (sheet = null)}>取消</button>
          <button class="btn-primary" type="submit" disabled={sheetBusy || !newProjectName.trim()}>
            创建
          </button>
        </div>
      </form>
    </BottomSheet>
  {/if}

  {#if sheet?.kind === 'rename'}
    <BottomSheet title="重命名" onClose={() => (sheet = null)}>
      <form on:submit|preventDefault={() => void submitRename(renameValue)}>
        <label class="field">
          <span>{sheet.scope === 'project' ? '项目名' : '对话标题'}</span>
          <input bind:value={renameValue} maxlength="80" autocomplete="off" />
        </label>
        {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
        <div class="sheet-actions">
          <button class="sheet-btn" type="button" on:click={() => (sheet = null)}>取消</button>
          <button class="btn-primary" type="submit" disabled={sheetBusy || !renameValue.trim()}>
            保存
          </button>
        </div>
      </form>
    </BottomSheet>
  {/if}

  {#if sheet?.kind === 'deleteProject'}
    <BottomSheet title="删除项目？" onClose={() => (sheet = null)}>
      <p>
        「{sheet.project.name}」及其全部对话与文件会被永久删除，无法恢复。
      </p>
      {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
      <div class="sheet-actions">
        <button class="sheet-btn" type="button" on:click={() => (sheet = null)}>取消</button>
        <button class="sheet-danger" type="button" disabled={sheetBusy} on:click={confirmDelete}>
          永久删除
        </button>
      </div>
    </BottomSheet>
  {/if}

  {#if sheet?.kind === 'deleteSession'}
    <BottomSheet title="删除对话？" onClose={() => (sheet = null)}>
      <p>「{sheet.session.title}」及其全部消息会被永久删除，无法恢复。</p>
      {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
      <div class="sheet-actions">
        <button class="sheet-btn" type="button" on:click={() => (sheet = null)}>取消</button>
        <button class="sheet-danger" type="button" disabled={sheetBusy} on:click={confirmDelete}>
          永久删除
        </button>
      </div>
    </BottomSheet>
  {/if}

  {#if sheet?.kind === 'deleteEntry'}
    <BottomSheet
      title={sheet.entry.type === 'directory' ? '删除文件夹？' : '删除文件？'}
      onClose={() => (sheet = null)}
    >
      <p>
        「{sheet.entry.name}」{sheet.entry.type === 'directory'
          ? '及其中全部文件'
          : ''}会被永久删除，无法恢复。
      </p>
      {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
      <div class="sheet-actions">
        <button class="sheet-btn" type="button" on:click={() => (sheet = null)}>取消</button>
        <button class="sheet-danger" type="button" disabled={sheetBusy} on:click={confirmDelete}>
          永久删除
        </button>
      </div>
    </BottomSheet>
  {/if}

  {#if sheet?.kind === 'password'}
    <BottomSheet
      title="修改密码"
      onClose={() => {
        sheet = null;
        passwordCurrent = passwordNext = passwordConfirm = '';
      }}
    >
      <form
        on:submit|preventDefault={() => {
          if (passwordNext !== passwordConfirm) {
            sheetError = '两次输入的新密码不一致';
            return;
          }
          void submitPassword(passwordCurrent, passwordNext);
        }}
      >
        <label class="field">
          <span>当前密码</span>
          <input type="password" autocomplete="current-password" bind:value={passwordCurrent} />
        </label>
        <label class="field">
          <span>新密码</span>
          <input type="password" autocomplete="new-password" bind:value={passwordNext} />
        </label>
        <label class="field">
          <span>确认新密码</span>
          <input type="password" autocomplete="new-password" bind:value={passwordConfirm} />
        </label>
        {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
        <div class="sheet-actions">
          <button
            class="sheet-btn"
            type="button"
            on:click={() => {
              sheet = null;
              passwordCurrent = passwordNext = passwordConfirm = '';
            }}>取消</button>
          <button
            class="btn-primary"
            type="submit"
            disabled={sheetBusy || !passwordCurrent || !passwordNext}
          >
            保存
          </button>
        </div>
      </form>
    </BottomSheet>
  {/if}

  {#if toast.text}
    <div class="toast" class:error={toast.error} role="status">{toast.text}</div>
  {/if}

  {#if lightbox}
    <div
      class="lightbox"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      aria-label={lightbox.alt || '图片预览'}
      use:focusOnMount
      on:click={() => (lightbox = null)}
      on:keydown={(event) => {
        if (event.key === 'Enter' || event.key === ' ' || event.key === 'Escape') lightbox = null;
      }}
    >
      <img src={lightbox.src} alt={lightbox.alt} />
    </div>
  {/if}
{/if}
