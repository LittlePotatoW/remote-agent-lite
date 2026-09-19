<script lang="ts">
  import type { ServerInfo } from '../lib/types';

  export let info: ServerInfo | null = null;
  export let onChangePassword: (current: string, next: string) => Promise<void>;
  export let onLogout: () => Promise<void>;
  export let onClose: () => void;

  let current = '';
  let next = '';
  let confirm = '';
  let message = '';
  let busy = false;

  async function changePassword() {
    if (next !== confirm) {
      message = '两次新密码不一致';
      return;
    }
    busy = true;
    message = '';
    try {
      await onChangePassword(current, next);
      message = '密码已更新';
      current = next = confirm = '';
    } catch (error) {
      message = error instanceof Error ? error.message : '修改失败';
    } finally {
      busy = false;
    }
  }
</script>

<div class="modal-backdrop" on:click={onClose} role="presentation">
  <div
    class="modal card"
    role="dialog"
    tabindex="0"
    on:click|stopPropagation
    on:keydown|stopPropagation
  >
    <div class="row">
      <h3 class="grow">设置</h3>
      <button class="btn small ghost" on:click={onClose}>关闭</button>
    </div>
    {#if info}
      <div class="card" style="padding:12px;margin-bottom:16px">
        <div class="section-title" style="margin:0 0 8px"><span>服务器</span></div>
        <div>内存可用：{info.memory.available_mb} MB / {info.memory.total_mb} MB</div>
        <div>磁盘可用：{info.disk.free_mb} MB / {info.disk.total_mb} MB</div>
        <div>负载：{info.cpu.load_1m.toFixed(2)} / {info.cpu.load_5m.toFixed(2)}</div>
        <div>建议并行度：{info.budgets.recommended_parallelism}</div>
        {#if info.budgets.warnings.length}
          <div style="color:var(--warning)">警告：{info.budgets.warnings.join('；')}</div>
        {/if}
      </div>
    {/if}
    <div class="field">
      <label for="current-password">当前密码</label>
      <input id="current-password" type="password" bind:value={current} />
    </div>
    <div class="field">
      <label for="new-password">新密码</label>
      <input id="new-password" type="password" bind:value={next} />
    </div>
    <div class="field">
      <label for="confirm-password">确认新密码</label>
      <input id="confirm-password" type="password" bind:value={confirm} />
    </div>
    {#if message}<div class="muted" style="margin-bottom:12px">{message}</div>{/if}
    <div class="row">
      <button class="btn primary" on:click={changePassword} disabled={busy || !current || !next}>修改密码</button>
      <button class="btn danger" on:click={onLogout}>退出登录</button>
    </div>
  </div>
</div>
