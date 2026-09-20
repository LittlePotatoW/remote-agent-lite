<script lang="ts">
  import { afterUpdate, onMount, tick } from 'svelte';
  import DOMPurify from 'dompurify';
  import { marked } from 'marked';
  import Icon from './Icon.svelte';
  import { downloadUrl, localProjectPath, mapImageSrc } from '../lib/media';
  import { prepareImages, revokeImages, type PendingImage } from '../lib/image';
  import type { Message, Project, Session } from '../lib/types';

  export let project: Project | null = null;
  export let session: Session | null = null;
  export let messages: Message[] = [];
  export let running = false;
  export let errorText = '';
  export let onSend: (prompt: string, images: PendingImage[]) => Promise<void>;
  export let onStop: () => void;
  export let onNewSession: () => void;
  export let onImage: (src: string, alt: string) => void;
  export let onOpenTree: () => void;
  export let onOpenFiles: () => void;

  marked.setOptions({ breaks: true, gfm: true });

  let prompt = '';
  let pending: PendingImage[] = [];
  let imageError = '';
  let dragging = false;
  let fileInput: HTMLInputElement;
  let sending = false;
  let scrollBox: HTMLDivElement;
  let textarea: HTMLTextAreaElement;
  let follow = true;
  let showJump = false;

  function render(content: string): string {
    const html = DOMPurify.sanitize(marked.parse(content || '') as string);
    if (!project) return html;
    const template = document.createElement('template');
    template.innerHTML = html;
    let changed = false;
    for (const img of Array.from(template.content.querySelectorAll('img'))) {
      const raw = img.getAttribute('src') || '';
      const mapped = mapImageSrc(raw, project.id);
      if (!mapped) {
        const local = localProjectPath(raw);
        if (!local) continue;
        const link = document.createElement('a');
        link.setAttribute('href', downloadUrl(project.id, local));
        link.textContent = img.getAttribute('alt') || local;
        img.replaceWith(link);
        changed = true;
        continue;
      }
      img.setAttribute('src', mapped);
      img.setAttribute('loading', 'lazy');
      img.setAttribute('decoding', 'async');
      img.setAttribute('class', 'chat-image');
      changed = true;
    }
    return changed ? template.innerHTML : html;
  }

  function handleBodyClick(event: MouseEvent) {
    const target = event.target as HTMLElement;
    if (target instanceof HTMLImageElement && target.classList.contains('chat-image')) {
      onImage(target.currentSrc || target.src, target.alt || '');
    }
  }

  function atBottom() {
    if (!scrollBox) return true;
    return scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight < 120;
  }

  function handleScroll() {
    follow = atBottom();
    showJump = !follow;
  }

  function scrollToBottom() {
    if (!scrollBox) return;
    scrollBox.scrollTop = scrollBox.scrollHeight;
    follow = true;
    showJump = false;
  }

  function grow() {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
  }

  afterUpdate(() => {
    if (follow) scrollToBottom();
  });

  onMount(() => {
    scrollToBottom();
    const viewport = window.visualViewport;
    if (!viewport) return;
    const sync = () => {
      const inset = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
      document.documentElement.style.setProperty('--keyboard', `${Math.round(inset)}px`);
    };
    viewport.addEventListener('resize', sync);
    viewport.addEventListener('scroll', sync);
    sync();
    return () => {
      viewport.removeEventListener('resize', sync);
      viewport.removeEventListener('scroll', sync);
      document.documentElement.style.removeProperty('--keyboard');
    };
  });

  async function addFiles(files: Iterable<File> | null | undefined) {
    const list = files ? Array.from(files) : [];
    if (!list.length || !session) return;
    const { images, errors } = await prepareImages(list);
    pending = [...pending, ...images];
    imageError = errors.join('；');
    await tick();
    grow();
  }

  function pickImages() {
    fileInput?.click();
  }

  function removeImage(id: string) {
    revokeImages(pending.filter((image) => image.id === id));
    pending = pending.filter((image) => image.id !== id);
    if (!pending.length) imageError = '';
  }

  function handleFileInput(event: Event) {
    const input = event.target as HTMLInputElement;
    void addFiles(input.files);
    input.value = '';
  }

  function handlePaste(event: ClipboardEvent) {
    const items = event.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (file) files.push(file);
    }
    if (!files.length) return;
    event.preventDefault();
    void addFiles(files);
  }

  function handleDragOver(event: DragEvent) {
    if (!session || !event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    dragging = true;
  }

  function handleDragLeave() {
    dragging = false;
  }

  function handleDrop(event: DragEvent) {
    dragging = false;
    if (!session || !event.dataTransfer?.files?.length) return;
    event.preventDefault();
    void addFiles(event.dataTransfer.files);
  }

  async function send() {
    const value = prompt.trim();
    if ((!value && pending.length === 0) || sending || !session) return;
    const outgoing = pending;
    sending = true;
    follow = true;
    try {
      await onSend(value, outgoing);
      prompt = '';
      pending = [];
      imageError = '';
      revokeImages(outgoing);
      await tick();
      grow();
    } catch {
      /* 失败信息由外层展示，输入内容与待发图片保留 */
    } finally {
      sending = false;
    }
  }

  function keydown(event: KeyboardEvent) {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    if (isTouchOnly()) return;
    event.preventDefault();
    void send();
  }

  function isTouchOnly(): boolean {
    return window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  }

</script>

<header class="chat-header">
  <div class="chat-side">
    <button class="icon-btn" type="button" aria-label="打开项目与对话列表" on:click={onOpenTree}>
      <Icon name="menu" size={22} />
    </button>
  </div>
  <div class="chat-heading">
    <strong>{session ? session.title : '新对话'}</strong>
    <span>{project ? project.name : '未选择项目'}</span>
  </div>
  <div class="chat-side right">
    <button
      class="icon-btn"
      type="button"
      aria-label="打开文件列表"
      disabled={!project}
      on:click={onOpenFiles}
    >
      <Icon name="folder" size={21} />
    </button>
    <button
      class="icon-btn"
      type="button"
      aria-label="新建对话"
      disabled={!project}
      on:click={onNewSession}
    >
      <Icon name="plus" size={22} />
    </button>
  </div>
</header>

<div class="chat-scroll" bind:this={scrollBox} on:scroll={handleScroll}>
  {#if !project}
    <div class="chat-empty">
      <div class="mark"><Icon name="folder" size={28} /></div>
      <strong>先去左边选一个项目</strong>
      <p>点左上角按钮，或向右滑动打开项目列表</p>
    </div>
  {:else if messages.length === 0}
    <div class="chat-empty">
      <div class="mark"><Icon name="terminal" size={28} /></div>
      <strong>随时开始</strong>
      <p>在下面描述你想做的事，Codex 会在服务器上完成</p>
    </div>
  {:else}
    {#each messages as message (message.id)}
      {#if message.role === 'user'}
        <div class="msg user">
          <div class="bubble">{message.content}</div>
        </div>
      {:else if message.role === 'system'}
        <div class="msg system">{message.content}</div>
      {:else}
        <div class="msg assistant" on:click={handleBodyClick} role="presentation">
          <div class="markdown">{@html render(message.content)}</div>
          {#if message.status === 'streaming'}<span class="streaming-caret"></span>{/if}
          {#if message.error}
            <p class="login-error" role="alert">{message.error}</p>
          {/if}
        </div>
      {/if}
    {/each}
  {/if}
</div>

{#if showJump && messages.length > 0}
  <button class="jump-latest" type="button" on:click={scrollToBottom}>
    <Icon name="chevronDown" size={16} />
    回到最新
  </button>
{/if}

<div class="composer">
  <div
    class="composer-card"
    class:dragging
    on:dragover={handleDragOver}
    on:dragleave={handleDragLeave}
    on:drop={handleDrop}
    role="presentation"
  >
    {#if pending.length > 0}
      <div class="pending-images">
        {#each pending as image (image.id)}
          <div class="pending-image">
            <button
              class="pending-thumb"
              type="button"
              aria-label="预览 {image.name}"
              on:click={() => onImage(image.previewUrl, image.name)}
            >
              <img src={image.previewUrl} alt={image.name} />
            </button>
            <button
              class="pending-remove"
              type="button"
              aria-label="移除 {image.name}"
              on:click={() => removeImage(image.id)}
            >
              <Icon name="close" size={11} stroke={2.2} />
            </button>
          </div>
        {/each}
      </div>
    {/if}
    <div class="composer-row">
      <button
        class="attach-btn"
        type="button"
        aria-label="添加图片"
        disabled={!session}
        on:click={pickImages}
      >
        <Icon name="image" size={20} />
      </button>
      <textarea
        bind:this={textarea}
        rows="1"
        placeholder={session ? '输入消息…' : '先选择一个对话'}
        aria-label="消息内容"
        bind:value={prompt}
        disabled={!session}
        enterkeyhint="enter"
        on:input={grow}
        on:keydown={keydown}
        on:paste={handlePaste}
      ></textarea>
      {#if running}
        <button class="send-btn" type="button" aria-label="停止" on:click={onStop}>
          <Icon name="stop" size={18} />
        </button>
      {:else}
        <button
          class="send-btn"
          type="button"
          aria-label="发送"
          disabled={!session || (!prompt.trim() && pending.length === 0)}
          on:click={send}
        >
          <Icon name="send" size={20} stroke={2} />
        </button>
      {/if}
    </div>
    <input
      bind:this={fileInput}
      class="file-input"
      type="file"
      accept="image/*"
      multiple
      on:change={handleFileInput}
    />
  </div>
  {#if errorText || imageError || sending}
    <div class="status-line" aria-live="polite">
      {#if errorText}<span style="color:var(--danger)">{errorText}</span>{/if}
      {#if imageError}<span style="color:var(--danger)">{imageError}</span>{/if}
      {#if sending}<span>发送中…</span>{/if}
    </div>
  {/if}
</div>
