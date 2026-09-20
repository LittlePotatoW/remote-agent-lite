<script lang="ts">
  import Icon from './Icon.svelte';

  export let setupRequired = false;
  export let error = '';
  export let busy = false;
  export let onSubmit: (password: string) => Promise<void>;

  let password = '';
  let confirm = '';
  let localError = '';

  async function submit() {
    localError = '';
    if (setupRequired && password !== confirm) {
      localError = '两次输入的密码不一致';
      return;
    }
    await onSubmit(password);
  }
</script>

<div class="login-page">
  <form class="login-card" on:submit|preventDefault={submit}>
    <div class="login-mark"><Icon name="terminal" size={28} /></div>
    <h1>remote-agent-lite</h1>
    <p>
      {setupRequired
        ? '第一次启动，先设置管理员密码，密码只以哈希形式保存在服务器本地。'
        : '输入管理员密码，进入你的远程 Codex 工作台。'}
    </p>
    <label class="field">
      <span>密码</span>
      <input
        type="password"
        name="password"
        autocomplete={setupRequired ? 'new-password' : 'current-password'}
        bind:value={password}
        disabled={busy}
      />
    </label>
    {#if setupRequired}
      <label class="field">
        <span>确认密码</span>
        <input
          type="password"
          name="confirm"
          autocomplete="new-password"
          bind:value={confirm}
          disabled={busy}
        />
      </label>
    {/if}
    {#if localError || error}
      <p class="login-error" role="alert">{localError || error}</p>
    {/if}
    <button class="btn-primary" type="submit" disabled={busy || !password}>
      {busy ? '处理中…' : setupRequired ? '设置密码并进入' : '登录'}
    </button>
  </form>
</div>
