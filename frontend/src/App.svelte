<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import Login from './components/Login.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import ChatPanel from './components/ChatPanel.svelte';
  import FilePanel from './components/FilePanel.svelte';
  import VersionPanel from './components/VersionPanel.svelte';
  import TrashPanel from './components/TrashPanel.svelte';
  import SettingsPanel from './components/SettingsPanel.svelte';
  import { ApiError, client, subscribeEvents, uploadFile } from './lib/api';
  import type {
    FileEntry,
    GitCommit,
    Message,
    Project,
    ServerInfo,
    Session
  } from './lib/types';

  let loading = true;
  let authenticated = false;
  let setupRequired = false;
  let loginError = '';
  let loginBusy = false;

  let projects: Project[] = [];
  let trash: Project[] = [];
  let sessions: Session[] = [];
  let activeProject: Project | null = null;
  let activeSession: Session | null = null;
  let messages: Message[] = [];
  let files: FileEntry[] = [];
  let filePath = '';
  let commits: GitCommit[] = [];
  let selectedCommit: {
    commit: string;
    stat: string;
    files: Array<Record<string, string>>;
  } | null = null;
  let serverInfo: ServerInfo | null = null;

  let folderTab: 'files' | 'versions' = 'files';
  let mobileView: 'chat' | 'files' | 'versions' | 'sessions' = 'chat';
  let showTrash = false;
  let showSettings = false;
  let fileLoading = false;
  let versionLoading = false;
  let uploading = new Map<string, { sent: number; total: number }>();
  let toast = '';
  let toastOk = false;
  let error = '';
  let busy = false;
  let statusText = '';
  let jobStatus: { running_job_id: string | null; queued_jobs: number } = {
    running_job_id: null,
    queued_jobs: 0
  };
  let eventSessionId = '';
  let cleanupEvents: (() => void) | null = null;
  let sessionRefreshTimer: number | null = null;

  $: busy = jobStatus.running_job_id !== null || jobStatus.queued_jobs > 0;
  $: statusText = jobStatus.running_job_id
    ? 'Codex 运行中'
    : jobStatus.queued_jobs > 0
      ? `排队中：${jobStatus.queued_jobs}`
      : '';
  $: if (authenticated && activeSession && activeSession.id !== eventSessionId) {
    eventSessionId = activeSession.id;
    bindEvents(activeSession.id);
  }

  onMount(async () => {
    try {
      const result = await client.status();
      setupRequired = result.setup_required;
      authenticated = result.authenticated;
      if (authenticated) await enterApp();
    } catch (err) {
      loginError = messageOf(err);
    } finally {
      loading = false;
    }
  });

  onDestroy(() => {
    cleanupEvents?.();
    if (sessionRefreshTimer) window.clearTimeout(sessionRefreshTimer);
  });

  function messageOf(errorValue: unknown): string {
    if (errorValue instanceof ApiError) return errorValue.message;
    if (errorValue instanceof Error) return errorValue.message;
    return String(errorValue);
  }

  function notify(message: string, ok = false) {
    toast = message;
    toastOk = ok;
    window.setTimeout(() => {
      if (toast === message) toast = '';
    }, 3200);
  }

  async function submitLogin(password: string) {
    loginBusy = true;
    loginError = '';
    try {
      if (setupRequired) await client.setup(password);
      else await client.login(password);
      authenticated = true;
      await enterApp();
    } catch (errorValue) {
      loginError = messageOf(errorValue);
    } finally {
      loginBusy = false;
    }
  }

  async function enterApp() {
    await Promise.all([refreshProjects(), refreshServerInfo()]);
    if (!activeProject && projects.length) await selectProject(projects[0].id);
  }

  async function refreshProjects() {
    const current = activeProject?.id;
    projects = await client.projects();
    activeProject = current ? projects.find((project) => project.id === current) || null : null;
  }

  async function refreshServerInfo() {
    try {
      serverInfo = await client.serverInfo();
    } catch {
      serverInfo = null;
    }
  }

  async function refreshSessions() {
    if (!activeProject) return;
    const current = activeSession?.id;
    sessions = await client.sessions(activeProject.id);
    if (current) activeSession = sessions.find((session) => session.id === current) || activeSession;
  }

  function scheduleSessionsRefresh() {
    if (sessionRefreshTimer) window.clearTimeout(sessionRefreshTimer);
    sessionRefreshTimer = window.setTimeout(() => {
      void refreshSessions();
      void refreshProjects();
    }, 400);
  }

  async function selectProject(projectId: string) {
    activeProject = projects.find((project) => project.id === projectId) || null;
    activeSession = null;
    eventSessionId = '';
    cleanupEvents?.();
    messages = [];
    files = [];
    filePath = '';
    commits = [];
    selectedCommit = null;
    if (!activeProject) return;
    sessions = await client.sessions(activeProject.id);
    if (!sessions.length) {
      await client.createSession(activeProject.id);
      sessions = await client.sessions(activeProject.id);
    }
    if (sessions.length) await selectSession(sessions[0].id);
    mobileView = 'chat';
  }

  async function selectSession(sessionId: string) {
    activeSession = sessions.find((session) => session.id === sessionId) || null;
    if (!activeSession) return;
    await Promise.all([loadMessages(), refreshStatus(), loadFiles(''), loadCommits()]);
    mobileView = 'chat';
  }

  async function loadMessages() {
    if (!activeSession) return;
    const result = await client.messages(activeSession.id);
    messages = result.messages;
    activeSession = result.session;
  }

  async function refreshStatus() {
    if (!activeSession) {
      jobStatus = { running_job_id: null, queued_jobs: 0 };
      return;
    }
    try {
      jobStatus = await client.sessionStatus(activeSession.id);
    } catch {
      jobStatus = { running_job_id: null, queued_jobs: 0 };
    }
  }

  async function createProject(name: string) {
    error = '';
    try {
      const project = await client.createProject(name);
      await refreshProjects();
      await selectProject(project.id);
      notify('项目已创建', true);
    } catch (errorValue) {
      error = messageOf(errorValue);
      notify(error, false);
    }
  }

  async function trashProject() {
    if (!activeProject) return;
    if (!window.confirm(`把项目“${activeProject.name}”移入回收站？`)) return;
    try {
      await client.trashProject(activeProject.id);
      activeProject = null;
      activeSession = null;
      messages = [];
      files = [];
      await refreshProjects();
      if (projects.length) await selectProject(projects[0].id);
      notify('项目已移入回收站', true);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function createSession() {
    if (!activeProject) return;
    try {
      const session = await client.createSession(activeProject.id);
      await refreshSessions();
      await selectSession(session.id);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function deleteSession(session: Session) {
    if (!window.confirm(`删除会话“${session.title}”？项目文件不会被删除。`)) return;
    try {
      await client.deleteSession(session.id);
      const wasActive = activeSession?.id === session.id;
      await refreshSessions();
      if (wasActive) {
        if (sessions.length) await selectSession(sessions[0].id);
        else {
          activeSession = null;
          messages = [];
        }
      }
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function sendMessage(prompt: string) {
    if (!activeSession) return;
    error = '';
    try {
      await client.send(activeSession.id, prompt);
      await loadMessages();
      await refreshStatus();
      await refreshSessions();
    } catch (errorValue) {
      error = messageOf(errorValue);
    }
  }

  async function stopTurn() {
    if (!activeSession) return;
    try {
      await client.interrupt(activeSession.id);
      await refreshStatus();
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function loadFiles(path: string) {
    if (!activeProject) return;
    fileLoading = true;
    try {
      const result = await client.files(activeProject.id, path);
      filePath = result.path;
      files = result.entries;
    } catch (errorValue) {
      notify(messageOf(errorValue));
    } finally {
      fileLoading = false;
    }
  }

  async function uploadFiles(fileList: FileList) {
    if (!activeProject) return;
    for (const file of Array.from(fileList)) {
      uploading.set(file.name, { sent: 0, total: file.size });
      uploading = new Map(uploading);
      try {
        await uploadFile(activeProject.id, file, (sent, total) => {
          uploading.set(file.name, { sent, total });
          uploading = new Map(uploading);
        });
        uploading.delete(file.name);
        uploading = new Map(uploading);
        notify(`${file.name} 上传完成`, true);
      } catch (errorValue) {
        uploading.delete(file.name);
        uploading = new Map(uploading);
        notify(`${file.name} 上传失败：${messageOf(errorValue)}`);
      }
    }
    await loadFiles(filePath);
    await refreshProjects();
  }

  async function deleteFile(entry: FileEntry) {
    if (!activeProject) return;
    if (!window.confirm(`删除文件“${entry.name}”？此操作不可恢复。`)) return;
    try {
      await client.deleteFile(activeProject.id, entry.path);
      await loadFiles(filePath);
      await loadCommits();
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function loadCommits() {
    if (!activeProject) return;
    versionLoading = true;
    try {
      commits = await client.gitLog(activeProject.id);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    } finally {
      versionLoading = false;
    }
  }

  async function selectCommit(commit: GitCommit) {
    if (!activeProject) return;
    try {
      selectedCommit = await client.gitShow(activeProject.id, commit.hash);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function restoreCommit(commit: GitCommit) {
    if (!activeProject) return;
    if (!window.confirm(`恢复到版本 ${commit.hash.slice(0, 10)}？当前未提交改动会被覆盖。`)) return;
    try {
      await client.gitRestore(activeProject.id, commit.hash);
      await Promise.all([loadFiles(filePath), loadCommits()]);
      notify('已恢复到选定版本', true);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function openTrash() {
    try {
      trash = await client.trash();
      showTrash = true;
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function restoreTrashed(project: Project) {
    try {
      await client.restoreProject(project.id);
      trash = await client.trash();
      await refreshProjects();
      notify('项目已恢复', true);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function purgeTrashed(project: Project) {
    if (!window.confirm(`彻底删除“${project.name}”？无法恢复。`)) return;
    try {
      await client.purgeProject(project.id);
      trash = await client.trash();
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function emptyTrash() {
    if (!window.confirm('清空回收站？所有项目将永久删除。')) return;
    try {
      await client.emptyTrash();
      trash = [];
      await refreshProjects();
      notify('回收站已清空', true);
    } catch (errorValue) {
      notify(messageOf(errorValue));
    }
  }

  async function openSettings() {
    await refreshServerInfo();
    showSettings = true;
  }

  async function changePassword(current: string, next: string) {
    await client.changePassword(current, next);
  }

  async function logout() {
    await client.logout();
    authenticated = false;
    activeProject = null;
    activeSession = null;
    messages = [];
    showSettings = false;
  }

  function bindEvents(sessionId: string) {
    cleanupEvents?.();
    cleanupEvents = subscribeEvents(sessionId, {
      'message.delta': (payload) => applyDelta(payload),
      'message.completed': (payload) => applyCompleted(payload),
      'turn.status': () => {
        void refreshStatus();
        scheduleSessionsRefresh();
      },
      'session.status': () => scheduleSessionsRefresh(),
      'files.changed': (payload) => {
        if (payload.project_id === activeProject?.id) {
          void loadFiles(filePath);
          void loadCommits();
          void refreshProjects();
        }
      },
      'server.warning': (payload) => notify(String(payload.message || 'Server warning'))
    });
  }

  function applyDelta(payload: Record<string, unknown>) {
    const messageId = String(payload.message_id || '');
    const delta = String(payload.delta || '');
    if (!messageId || !delta) return;
    const index = messages.findIndex((message) => message.id === messageId);
    if (index >= 0) {
      const next = [...messages];
      next[index] = {
        ...next[index],
        content: next[index].content + delta,
        status: 'streaming'
      };
      messages = next;
    } else {
      messages = [
        ...messages,
        {
          id: messageId,
          session_id: activeSession?.id || '',
          seq: Number(payload.seq || 0),
          role: 'assistant',
          content: delta,
          status: 'streaming',
          created_at: new Date().toISOString()
        }
      ];
    }
  }

  function applyCompleted(payload: Record<string, unknown>) {
    const messageId = String(payload.message_id || '');
    const index = messages.findIndex((message) => message.id === messageId);
    if (index >= 0) {
      const next = [...messages];
      next[index] = {
        ...next[index],
        content: String(payload.content ?? next[index].content),
        status: String(payload.status || 'completed'),
        error: payload.error ? String(payload.error) : null
      };
      messages = next;
    }
    scheduleSessionsRefresh();
    void refreshStatus();
  }
</script>

{#if loading}
  <div class="center-screen"><div class="muted">加载中…</div></div>
{:else if !authenticated}
  <Login
    setupRequired={setupRequired}
    error={loginError}
    busy={loginBusy}
    onSubmit={submitLogin}
  />
{:else}
  <div class="app-shell">
    <Sidebar
      {projects}
      activeProjectId={activeProject?.id || ''}
      {sessions}
      activeSessionId={activeSession?.id || ''}
      mobileVisible={mobileView === 'sessions'}
      onSelectProject={selectProject}
      onCreateProject={createProject}
      onSelectSession={selectSession}
      onCreateSession={createSession}
      onDeleteSession={deleteSession}
      onOpenTrash={openTrash}
      onOpenSettings={openSettings}
    />

    <main class="main-panel" class:hidden-mobile={mobileView !== 'chat'}>
      <div class="main-tabs">
        <strong class="grow">{activeProject?.name || '请选择项目'}</strong>
        <button class="tab" class:active={folderTab === 'files'} on:click={() => (folderTab = 'files')}>
          文件
        </button>
        <button class="tab" class:active={folderTab === 'versions'} on:click={() => (folderTab = 'versions')}>
          版本
        </button>
        {#if activeProject}
          <button class="btn small ghost danger" on:click={trashProject}>删除项目</button>
        {/if}
      </div>
      <div class="main-body">
        <ChatPanel
          session={activeSession}
          {messages}
          {statusText}
          {busy}
          {error}
          onSend={sendMessage}
          onStop={stopTurn}
        />
      </div>
    </main>

    {#if folderTab === 'files'}
      <FilePanel
        project={activeProject}
        entries={files}
        path={filePath}
        loading={fileLoading}
        progress={uploading}
        mobileVisible={mobileView === 'files'}
        onNavigate={loadFiles}
        onUpload={uploadFiles}
        onDelete={deleteFile}
        onRefresh={() => loadFiles(filePath)}
      />
    {:else}
      <VersionPanel
        project={activeProject}
        {commits}
        selected={selectedCommit}
        loading={versionLoading}
        mobileVisible={mobileView === 'versions'}
        onSelect={selectCommit}
        onRestore={restoreCommit}
        onRefresh={loadCommits}
      />
    {/if}

    <nav class="mobile-nav">
      <button class:active={mobileView === 'chat'} on:click={() => (mobileView = 'chat')}>对话</button>
      <button
        class:active={mobileView === 'files'}
        on:click={() => {
          folderTab = 'files';
          mobileView = 'files';
        }}>文件</button>
      <button
        class:active={mobileView === 'versions'}
        on:click={() => {
          folderTab = 'versions';
          mobileView = 'versions';
        }}>版本</button>
      <button class:active={mobileView === 'sessions'} on:click={() => (mobileView = 'sessions')}>会话</button>
      <button on:click={openSettings}>设置</button>
    </nav>
  </div>
{/if}

{#if showTrash}
  <TrashPanel
    {trash}
    onRestore={restoreTrashed}
    onPurge={purgeTrashed}
    onEmpty={emptyTrash}
    onClose={() => (showTrash = false)}
  />
{/if}

{#if showSettings}
  <SettingsPanel
    info={serverInfo}
    onChangePassword={changePassword}
    onLogout={logout}
    onClose={() => (showSettings = false)}
  />
{/if}

{#if toast}
  <div class="toast" class:ok={toastOk}>{toast}</div>
{/if}
