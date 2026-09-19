<script lang="ts">
  import { afterUpdate } from 'svelte';
  import DOMPurify from 'dompurify';
  import { marked } from 'marked';
  import type { Message, Session } from '../lib/types';
  import { formatTime } from '../lib/format';

  export let session: Session | null = null;
  export let messages: Message[] = [];
  export let statusText = '';
  export let busy = false;
  export let error = '';
  export let onSend: (prompt: string) => Promise<void>;
  export let onStop: () => Promise<void>;

  let prompt = '';
  let scrollBox: HTMLDivElement;
  let sending = false;

  marked.setOptions({ breaks: true, gfm: true });

  function render(content: string): string {
    return DOMPurify.sanitize(marked.parse(content || '') as string);
  }

  async function send() {
    const value = prompt.trim();
    if (!value || sending || !session) return;
    sending = true;
    try {
      await onSend(value);
      prompt = '';
    } finally {
      sending = false;
    }
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  afterUpdate(() => {
    if (scrollBox) scrollBox.scrollTop = scrollBox.scrollHeight;
  });
</script>

<div class="chat-wrap">
  <div class="chat-messages" bind:this={scrollBox}>
    {#if !session}
      <div class="empty">选择或创建一个会话开始对话</div>
    {:else if messages.length === 0}
      <div class="empty">
        <div>开始和这个项目的 Codex 对话。</div>
        <div class="muted">文件请先在右侧“文件”面板上传到 <code>uploads/</code>。</div>
      </div>
    {/if}
    {#each messages as message (message.id)}
      <div class="message {message.role}" class:streaming={message.status === 'streaming'}>
        <div class="bubble">
          <div class="meta">
            {message.role === 'user' ? '你' : message.role === 'assistant' ? 'Codex' : '系统'}
            · {formatTime(message.created_at)}
            {#if message.status === 'failed' && message.error}· 失败{/if}
          </div>
          {#if message.role === 'user'}
            <div style="white-space:pre-wrap">{message.content}</div>
          {:else}
            <div class="markdown">{@html render(message.content)}</div>
          {/if}
          {#if message.error}
            <div style="color:var(--danger);font-size:12px;margin-top:6px">{message.error}</div>
          {/if}
        </div>
      </div>
    {/each}
  </div>

  <div class="composer">
    <div class="composer-inner">
      <textarea
        rows="2"
        placeholder={session ? '输入消息，Enter 发送，Shift+Enter 换行' : '先选择一个会话'}
        bind:value={prompt}
        disabled={!session || sending}
        on:keydown={keydown}
      ></textarea>
      <button class="btn primary" on:click={send} disabled={!session || sending || !prompt.trim()}>
        发送
      </button>
      <button class="btn danger" on:click={onStop} disabled={!busy}>停止</button>
    </div>
    <div class="status-row">
      {#if statusText}<span>{statusText}</span>{/if}
      {#if error}<span style="color:var(--danger)">{error}</span>{/if}
    </div>
  </div>
</div>

