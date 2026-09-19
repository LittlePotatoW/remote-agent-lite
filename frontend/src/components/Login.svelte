<script lang="ts">
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

<div class="center-screen">
  <form class="card login-card" on:submit|preventDefault={submit}>
    <h1>remote-agent-lite</h1>
    <p>
      {setupRequired
        ? '第一次启动：设置管理员密码。该密码只在服务器本地保存哈希。'
        : '输入管理员密码进入你的远程 Codex 工作台。'}
    </p>
    <div class="field">
      <label for="password">密码</label>
      <input
        id="password"
        type="password"
        autocomplete={setupRequired ? 'new-password' : 'current-password'}
        bind:value={password}
        disabled={busy}
      />
    </div>
    {#if setupRequired}
      <div class="field">
        <label for="confirm">确认密码</label>
        <input id="confirm" type="password" bind:value={confirm} disabled={busy} />
      </div>
    {/if}
    {#if localError || error}
      <p class="error-text" style="color: var(--danger)">{localError || error}</p>
    {/if}
    <button class="btn primary" type="submit" disabled={busy || !password}>
      {busy ? '处理中…' : setupRequired ? '设置密码并进入' : '登录'}
    </button>
  </form>
</div>

