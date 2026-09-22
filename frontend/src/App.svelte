<script lang="ts">
  import { onMount } from 'svelte';
  import ActionMenu from './components/ActionMenu.svelte';
  import BottomSheet from './components/BottomSheet.svelte';
  import ChatView from './components/ChatView.svelte';
  import FilesPanel from './components/FilesPanel.svelte';
  import Icon from './components/Icon.svelte';
  import Login from './components/Login.svelte';
  import SchedulePage from './components/SchedulePage.svelte';
  import SettingsView from './components/SettingsView.svelte';
  import TreePanel from './components/TreePanel.svelte';
  import Wheel from './components/Wheel.svelte';
  import { ApiError, client, subscribeEvents, uploadFile } from './lib/api';
  import { applyTheme, readTheme } from './lib/theme';
  import { isImagePath, rawImageUrl } from './lib/media';
  import { prepareImages, revokeImages, type PendingImage } from './lib/image';
  import {
    HOUR_OPTIONS,
    MINUTE_OPTIONS,
    MONTH_OPTIONS,
    WEEKDAYS,
    dayOptions,
    defaultLoop,
    defaultOnce
  } from './lib/schedule';
  import { swipeAxis, swipeKeepsOpen, swipeStartedAtEdge } from './lib/swipe';
  import type {
    FileEntry,
    MenuItem,
    Message,
    Project,
    ScheduledTask,
    ServerInfo,
    Session,
    ThemeName
  } from './lib/types';

  type Panel = 'tree' | 'files' | null;
  /** 手势正在拖动的那一层；'schedule' 是盖在最上面的整屏定时任务页。 */
  type DragSide = 'tree' | 'files' | 'schedule';
  type Sheet =
    | { kind: 'newProject' }
    | { kind: 'rename'; scope: 'project' | 'session' | 'task'; id: string; value: string }
    | { kind: 'deleteProject'; project: Project }
    | { kind: 'deleteSession'; session: Session }
    | { kind: 'deleteTask'; task: ScheduledTask }
    | { kind: 'deleteEntry'; entry: FileEntry }
    | { kind: 'newTask'; session: Session }
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
  let side: DragSide = 'tree';

  // 定时任务页：整屏盖在列表之上，和主页同一个层级；左滑退回列表。
  let scheduleSessionId = '';
  let schedulePull = 0;
  let scheduleTasks: ScheduledTask[] = [];
  let scheduleLoading = false;

  let taskPrompt = '';
  /** 表单第一层：定时（一次性）还是循环。 */
  let taskRepeat: 'once' | 'loop' = 'once';
  /** 表单第二层：循环的粒度。 */
  let taskFreq: 'daily' | 'weekly' | 'monthly' = 'daily';
  let taskMonth = 1;
  let taskDay = 1;
  let taskHour = 9;
  let taskMinute = 0;
  let taskWeekday = 5;
  let taskMonthDays = dayOptions(1);
  /** 这条定时任务要随正文一起发的图片（和输入框一样，只在本地预览）。 */
  let taskImages: PendingImage[] = [];
  let taskImageError = '';
  let taskImageInput: HTMLInputElement;

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
  let startEdge = false;
  let moveTime = 0;
  let prevDx = 0;
  let lastDx = 0;
  let recentVelocity = 0;
  let axis: 'idle' | 'pending' | 'horizontal' | 'vertical' = 'idle';
  let base = 0;

  $: activeProject = projects.find((item) => item.id === activeProjectId) ?? null;
  $: activeSession = activeProject?.sessions.find((item) => item.id === activeSessionId) ?? null;
  $: scheduleSession =
    projects
      .flatMap((project) => project.sessions)
      .find((item) => item.id === scheduleSessionId) ?? null;
  $: scheduleProject =
    projects.find((item) => item.id === scheduleSession?.project_id) ?? activeProject;
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
    if (scheduleSessionId && !scheduleSession) scheduleSessionId = '';
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

  async function selectSession(session: Session, keepPanel = false) {
    if (session.project_id && session.project_id !== activeProjectId) {
      activeProjectId = session.project_id;
      localStorage.setItem('ral-project', session.project_id);
    }
    activeSessionId = session.id;
    chatError = '';
    if (!keepPanel) closePanel();
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
      'turn.status': (payload) => {
        // 终态时补一次全量拉取：即使 message.completed 事件被丢弃也能自愈
        const status = String(payload.status || '');
        if (status === 'succeeded' || status === 'failed' || status === 'interrupted') {
          void loadMessages();
        }
        void refreshStatus();
        scheduleOverview();
        refreshScheduleTasks(payload);
      },
      resync: () => {
        // 服务端提示订阅队列丢过事件，重新拉取权威状态
        void loadMessages();
        void refreshStatus();
        scheduleOverview();
        void loadScheduleTasks();
      },
      'session.status': (payload) => {
        scheduleOverview();
        refreshScheduleTasks(payload);
      },
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

  async function sendMessage(prompt: string, images: PendingImage[] = []) {
    if (!activeSessionId) return;
    chatError = '';
    try {
      await client.send(
        activeSessionId,
        prompt,
        images.map((image) => ({ name: image.name, data_url: image.dataUrl }))
      );
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

  /* ---------- 定时任务页 ---------- */

  function schedulePageWidth() {
    return window.innerWidth;
  }

  async function openSchedule(session: Session) {
    scheduleSessionId = session.id;
    scheduleTasks = [];
    // 列表留在原地不关：定时任务页只是盖在它上面，左滑就滑回来
    schedulePull = 0;
    await loadScheduleTasks();
    // 先渲染在屏幕外、下一帧再拉到整屏，滑入动画才有起点
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        if (scheduleSessionId === session.id) schedulePull = schedulePageWidth();
      })
    );
  }

  function closeSchedule() {
    schedulePull = 0;
    const closing = scheduleSessionId;
    // 等滑出动画结束再卸载，不然会看到页面「跳」一下
    window.setTimeout(() => {
      if (scheduleSessionId === closing && schedulePull === 0) scheduleSessionId = '';
    }, 260);
  }

  async function loadScheduleTasks() {
    if (!scheduleSessionId) return;
    scheduleLoading = true;
    try {
      const result = await client.scheduledTasks(scheduleSessionId);
      scheduleTasks = result.tasks;
    } catch (error) {
      notify(messageOf(error), true);
    } finally {
      scheduleLoading = false;
    }
  }

  /** 定时任务跑掉之后会被自动删除，事件到了就顺手刷新这一页。 */
  function refreshScheduleTasks(payload: Record<string, unknown>) {
    if (!scheduleSessionId) return;
    if (payload.session_id && payload.session_id !== scheduleSessionId) return;
    void loadScheduleTasks();
  }

  function openTaskMenu(task: ScheduledTask, event: MouseEvent) {
    menu = {
      anchor: anchorOf(event),
      items: [
        {
          label: '重命名',
          icon: 'pencil',
          onSelect: () => (sheet = { kind: 'rename', scope: 'task', id: task.id, value: task.title })
        },
        {
          label: task.pinned ? '取消置顶' : '置顶',
          icon: 'pin',
          onSelect: () => void setTaskPinned(task, !task.pinned)
        },
        {
          label: '删除',
          icon: 'trash',
          danger: true,
          onSelect: () => (sheet = { kind: 'deleteTask', task })
        }
      ]
    };
  }

  async function setTaskPinned(task: ScheduledTask, pinned: boolean) {
    try {
      await client.updateScheduledTask(task.id, { pinned });
      await loadScheduleTasks();
    } catch (error) {
      notify(messageOf(error), true);
    }
  }

  function openNewTaskForm(session: Session) {
    taskPrompt = '';
    taskRepeat = 'once';
    taskFreq = 'daily';
    applyOnceDefaults();
    dropTaskImages();
    sheetError = '';
    sheet = { kind: 'newTask', session };
  }

  /** 关掉表单时把没发出去的图片对象释放掉。 */
  function closeNewTaskForm() {
    dropTaskImages();
    sheetError = '';
    sheet = null;
  }

  function dropTaskImages() {
    revokeImages(taskImages);
    taskImages = [];
    taskImageError = '';
  }

  async function pickTaskImages(files: FileList) {
    const { images, errors } = await prepareImages(files);
    taskImages = [...taskImages, ...images];
    taskImageError = errors.join('；');
  }

  function removeTaskImage(id: string) {
    revokeImages(taskImages.filter((image) => image.id === id));
    taskImages = taskImages.filter((image) => image.id !== id);
    if (!taskImages.length) taskImageError = '';
  }

  /** 「定时」那组滚轮的默认值：一小时后（取整到 5 分钟）。 */
  function applyOnceDefaults() {
    const once = defaultOnce();
    taskMonth = once.month;
    taskDay = once.day;
    taskHour = once.hour;
    taskMinute = once.minute;
  }

  /** 切到「循环」时把时间复位成 09:00，星期/日期跟着今天。 */
  function applyLoopDefaults() {
    const loop = defaultLoop();
    taskHour = loop.hour;
    taskMinute = loop.minute;
    taskWeekday = loop.weekday;
    taskDay = loop.day;
    taskFreq = 'daily';
  }

  function pickRepeat(next: 'once' | 'loop') {
    if (next === taskRepeat) return;
    taskRepeat = next;
    if (next === 'once') applyOnceDefaults();
    else applyLoopDefaults();
  }

  /** 「日」滚轮的可选范围：定时跟着月份走，循环固定 1–31。 */
  $: taskMonthDays = taskRepeat === 'once' ? dayOptions(taskMonth) : dayOptions(null);
  $: if (taskDay > taskMonthDays.length) taskDay = taskMonthDays.length;

  async function submitNewTask() {
    if (!sheet || sheet.kind !== 'newTask') return;
    const prompt = taskPrompt.trim();
    if (!prompt && taskImages.length === 0) {
      sheetError = '内容不能为空';
      return;
    }
    sheetBusy = true;
    sheetError = '';
    try {
      // 表单里填的是服务器本地时间，原样发过去由服务端解释
      const when = { hour: taskHour, minute: taskMinute };
      const photos = taskImages.map((image) => ({ name: image.name, data_url: image.dataUrl }));
      const body =
        taskRepeat === 'once'
          ? { prompt, kind: 'once' as const, month: taskMonth, day: taskDay, ...when, images: photos }
          : taskFreq === 'weekly'
            ? { prompt, kind: 'weekly' as const, weekday: taskWeekday, ...when, images: photos }
            : taskFreq === 'monthly'
              ? { prompt, kind: 'monthly' as const, day: taskDay, ...when, images: photos }
              : { prompt, kind: 'daily' as const, ...when, images: photos };
      await client.createScheduledTask(sheet.session.id, body);
      closeNewTaskForm();
      await Promise.all([loadScheduleTasks(), refreshOverview()]);
      notify('定时任务已创建');
    } catch (error) {
      sheetError = messageOf(error);
    } finally {
      sheetBusy = false;
    }
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
    // 列表行也要能起手拖动（面板展开时基本只能按在行上），只放过真正的控件。
    if (target.closest('input, textarea, .sheet, .menu, .settings-page, .row-tail, .icon-btn'))
      return;
    startX = event.clientX;
    startY = event.clientY;
    startEdge = swipeStartedAtEdge(startX, window.innerWidth);
    axis = 'pending';
    if (scheduleSessionId) {
      // 定时任务页盖在最上面，这一拖只可能是在滑它
      base = schedulePull;
      side = 'schedule';
    } else {
      base = panel ? panelWidth() : 0;
      side = panel ?? 'tree';
    }
    moveTime = performance.now();
    prevDx = 0;
    lastDx = 0;
    recentVelocity = 0;
  }

  function onPointerMove(event: PointerEvent) {
    if (axis === 'idle') return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (axis === 'pending') {
      const decision = swipeAxis(dx, dy, startEdge);
      if (decision === 'pending') return;
      if (decision === 'vertical') {
        axis = 'vertical';
        return;
      }
      axis = 'horizontal';
      dragging = true;
      if (!scheduleSessionId && !panel) side = dx > 0 ? 'tree' : 'files';
    }
    if (axis !== 'horizontal') return;
    // 记录最近一段的位移速度，用来识别快速轻扫。
    const now = performance.now();
    const elapsed = now - moveTime;
    if (elapsed > 0) {
      recentVelocity = (dx - prevDx) / elapsed;
      moveTime = now;
      prevDx = dx;
    }
    lastDx = dx;
    const width = side === 'schedule' ? schedulePageWidth() : panelWidth();
    // tree / schedule 都在左边，往左拖是收起
    const next = side === 'files' ? base - dx : base + dx;
    const value = Math.max(0, Math.min(width, next));
    if (side === 'schedule') schedulePull = value;
    else pull = value;
  }

  function onPointerUp() {
    if (axis === 'horizontal') {
      if (side === 'schedule') {
        const width = schedulePageWidth();
        const keep = swipeKeepsOpen({
          pull: schedulePull,
          base,
          panel: 'schedule',
          lastDx,
          recentVelocity,
          width
        });
        if (keep) schedulePull = width;
        else closeSchedule();
        dragEndedAt = Date.now();
      } else {
        const width = panelWidth();
        const keep = swipeKeepsOpen({ pull, base, panel, lastDx, recentVelocity, width });
        if (keep) {
          panel = side === 'files' ? 'files' : 'tree';
          pull = width;
        } else {
          panel = null;
          pull = 0;
        }
        dragEndedAt = Date.now();
      }
    }
    axis = 'idle';
    dragging = false;
    if (side !== 'schedule') pull = panel ? panelWidth() : 0;
    lastDx = 0;
    recentVelocity = 0;
  }

  // 拖动结束后紧跟的 click 是手势的尾巴，不该被当成点击。
  function onClickCapture(event: MouseEvent) {
    if (Date.now() - dragEndedAt < 250) {
      event.preventDefault();
      event.stopPropagation();
    }
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
      else if (sheet?.kind === 'newTask') closeNewTaskForm();
      else if (sheet) sheet = null;
      else if (scheduleSessionId) closeSchedule();
      else if (showSettings) showSettings = false;
      else if (panel) closePanel();
      return;
    }
    if (event.key === 'ArrowRight') {
      if (scheduleSessionId) return;
      event.preventDefault();
      if (panel === 'files') closePanel();
      else openPanel('tree');
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      if (scheduleSessionId) closeSchedule();
      else if (panel === 'tree') closePanel();
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
          label: '复制',
          icon: 'copy',
          onSelect: () => void duplicateSession(session)
        },
        {
          label: '定时任务',
          icon: 'clock',
          onSelect: () => void openSchedule(session)
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

  async function duplicateSession(session: Session) {
    try {
      const created = await client.duplicateSession(session.id);
      await refreshOverview();
      await selectSession(created.session);
      notify('已复制对话，上下文沿用原会话');
    } catch (error) {
      notify(messageOf(error), true);
    }
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
      } else if (sheet.scope === 'task') {
        await client.updateScheduledTask(sheet.id, { title: next });
        await loadScheduleTasks();
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
      } else if (sheet.kind === 'deleteTask') {
        await client.deleteScheduledTask(sheet.task.id);
        await Promise.all([loadScheduleTasks(), refreshOverview()]);
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
      class:dragging
      role="presentation"
      on:pointerdown={onPointerDown}
      on:pointermove={onPointerMove}
      on:pointerup={onPointerUp}
      on:pointercancel={onPointerUp}
      on:click|capture={onClickCapture}
    >
      <aside class="panel tree" style="--pull:{side === 'tree' ? pull : 0}px">
        <TreePanel
          {projects}
          {activeSessionId}
          onSelectSession={(_, session) => void selectSession(session, true)}
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
          onClose={closePanel}
        />
      </aside>

      <aside class="panel files" style="--pull:{side === 'files' ? pull : 0}px">
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
          onClose={closePanel}
        />
      </aside>

      <main
        class="page"
        style="--shift:{side === 'tree' ? pull : side === 'files' ? -pull : 0}px; --page-radius:{pull > 0 ? 20 : 0}px; --page-shadow:{pull > 0 ? 'var(--shadow-panel)' : 'none'}"
      >
        <ChatView
          project={activeProject}
          session={activeSession}
          {messages}
          {running}
          errorText={chatError}
          onSend={sendMessage}
          onStop={stopTurn}
          onImage={(src, alt) => (lightbox = { src, alt })}
          onOpenTree={() => openPanel('tree')}
          onOpenFiles={() => openPanel('files')}
          onNewSession={() => {
            if (activeProject) void createSessionIn(activeProject);
          }}
        />
      </main>

      <div class="scrim" class:visible={panel !== null} on:click={onScrimClick} role="presentation"></div>

      {#if scheduleSessionId}
        <section class="page-layer" style="--pull:{schedulePull}px">
          <SchedulePage
            project={scheduleProject}
            session={scheduleSession}
            tasks={scheduleTasks}
            loading={scheduleLoading}
            onClose={closeSchedule}
            onNew={() => scheduleSession && openNewTaskForm(scheduleSession)}
            onTaskMenu={openTaskMenu}
          />
        </section>
      {/if}
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
          <span>{sheet.scope === 'project' ? '项目名' : sheet.scope === 'task' ? '任务名' : '对话标题'}</span>
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

  {#if sheet?.kind === 'newTask'}
    <BottomSheet title="新建定时任务" onClose={closeNewTaskForm}>
      <form on:submit|preventDefault={() => void submitNewTask()}>
        <div class="field">
          <span>内容</span>
          <div class="field-card">
            {#if taskImages.length > 0}
              <div class="pending-images">
                {#each taskImages as image (image.id)}
                  <div class="pending-image">
                    <button
                      class="pending-thumb"
                      type="button"
                      aria-label="预览 {image.name}"
                      on:click={() => (lightbox = { src: image.previewUrl, alt: image.name })}
                    >
                      <img src={image.previewUrl} alt={image.name} />
                    </button>
                    <button
                      class="pending-remove"
                      type="button"
                      aria-label="移除 {image.name}"
                      on:click={() => removeTaskImage(image.id)}
                    >
                      <Icon name="close" size={11} stroke={2.2} />
                    </button>
                  </div>
                {/each}
              </div>
            {/if}
            <div class="field-card-row">
              <button
                class="attach-btn"
                type="button"
                aria-label="添加图片"
                on:click={() => taskImageInput?.click()}
              >
                <Icon name="image" size={20} />
              </button>
              <textarea class="field-area" rows="3" bind:value={taskPrompt}></textarea>
            </div>
          </div>
          <input
            bind:this={taskImageInput}
            type="file"
            accept="image/*"
            multiple
            class="sr-only"
            tabindex="-1"
            aria-hidden="true"
            on:change={(event) => {
              const files = (event.currentTarget as HTMLInputElement).files;
              if (files && files.length) void pickTaskImages(files);
              if (taskImageInput) taskImageInput.value = '';
            }}
          />
          {#if taskImageError}<p class="login-error" role="alert">{taskImageError}</p>{/if}
        </div>
        <div class="field">
          <span>重复</span>
          <div class="segmented">
            <button
              type="button"
              class:active={taskRepeat === 'once'}
              on:click={() => pickRepeat('once')}>定时</button>
            <button
              type="button"
              class:active={taskRepeat === 'loop'}
              on:click={() => pickRepeat('loop')}>循环</button>
          </div>
        </div>
        {#if taskRepeat === 'loop'}
          <div class="field">
            <span>频率</span>
            <div class="chips">
              <button
                type="button"
                class="chip"
                class:active={taskFreq === 'daily'}
                on:click={() => (taskFreq = 'daily')}>每天</button>
              <button
                type="button"
                class="chip"
                class:active={taskFreq === 'weekly'}
                on:click={() => (taskFreq = 'weekly')}>每周</button>
              <button
                type="button"
                class="chip"
                class:active={taskFreq === 'monthly'}
                on:click={() => (taskFreq = 'monthly')}>每月</button>
            </div>
          </div>
        {/if}
        {#if taskRepeat === 'loop' && taskFreq === 'weekly'}
          <div class="field">
            <span>星期</span>
            <div class="chips">
              {#each WEEKDAYS as day, index}
                <button
                  type="button"
                  class="chip"
                  class:active={taskWeekday === index}
                  on:click={() => (taskWeekday = index)}>{day}</button>
              {/each}
            </div>
          </div>
        {/if}
        <div class="field">
          <span>{taskRepeat === 'loop' && taskFreq === 'monthly' ? '日期' : '时间'}</span>
          <div class="wheel-row">
            <div class="wheel-band" aria-hidden="true"></div>
            {#if taskRepeat === 'once'}
              <Wheel label="月" options={MONTH_OPTIONS} bind:value={taskMonth} />
              <Wheel label="日" options={taskMonthDays} bind:value={taskDay} />
            {:else if taskFreq === 'monthly'}
              <Wheel label="日" options={taskMonthDays} bind:value={taskDay} />
            {/if}
            <Wheel label="时" options={HOUR_OPTIONS} bind:value={taskHour} />
            <Wheel label="分" options={MINUTE_OPTIONS} bind:value={taskMinute} />
          </div>
        </div>
        {#if sheetError}<p class="login-error" role="alert">{sheetError}</p>{/if}
        <div class="sheet-actions">
          <button class="sheet-btn" type="button" on:click={closeNewTaskForm}>取消</button>
          <button
            class="btn-primary"
            type="submit"
            disabled={sheetBusy || (!taskPrompt.trim() && taskImages.length === 0)}
          >
            创建
          </button>
        </div>
      </form>
    </BottomSheet>
  {/if}

  {#if sheet?.kind === 'deleteTask'}
    <BottomSheet title="删除定时任务？" onClose={() => (sheet = null)}>
      <p>「{sheet.task.title}」会被删除，到点不会再发消息。</p>
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
