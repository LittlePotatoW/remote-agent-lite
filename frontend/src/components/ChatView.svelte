<script lang="ts">
  import { afterUpdate, onMount, tick } from 'svelte';
  import DOMPurify from 'dompurify';
  import { marked } from 'marked';
  import Icon from './Icon.svelte';
  import { downloadUrl, localProjectPath, mapImageSrc } from '../lib/media';
  import type { Message, Project, Session } from '../lib/types';

  export let project: Project | null = null;
  export let session: Session | null = null;
  export let messages: Message[] = [];
  export let running = false;
  export let errorText = '';
  export let onSend: (prompt: string) => Promise<void>;
  export let onStop: () => void;
  export let onNewSession: () => void;
  export let onImage: (src: string, alt: string) => void;

  marked.setOptions({ breaks: true, gfm: true });

  let prompt = '';
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

  async function send() {
    const value = prompt.trim();
    if (!value || sending || !session) return;
    sending = true;
    follow = true;
    try {
      await onSend(value);
      prompt = '';
      await tick();
      grow();
    } catch {
      /* 失败信息由外层展示，输入内容保留 */
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
  <div class="chat-heading">
    <strong>{session ? session.title : '新对话'}</strong>
    <span>{project ? project.name : '未选择项目'}</span>
  </div>
  <button
    class="icon-btn"
    type="button"
    aria-label="新建对话"
    disabled={!project}
    on:click={onNewSession}
  >
    <Icon name="plus" size={22} />
  </button>
</header>

<div class="chat-scroll" bind:this={scrollBox} on:scroll={handleScroll}>
  {#if !project}
    <div class="chat-empty">
      <div class="mark"><Icon name="folder" size={28} /></div>
      <strong>先去左边选一个项目</strong>
      <p>手指向右滑动打开项目列表</p>
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
  <div class="composer-card">
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
        disabled={!session || !prompt.trim()}
        on:click={send}
      >
        <Icon name="send" size={20} stroke={2} />
      </button>
    {/if}
  </div>
  {#if errorText || sending}
    <div class="status-line" aria-live="polite">
      {#if errorText}<span style="color:var(--danger)">{errorText}</span>{/if}
    </div>
  {/if}
</div>
