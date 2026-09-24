<script lang="ts">
  import Icon from './Icon.svelte';
  import { THEME_OPTIONS } from '../lib/theme';
  import type { ServerInfo, ThemeName } from '../lib/types';

  export let info: ServerInfo | null = null;
  export let theme: ThemeName = 'blue';
  export let onTheme: (name: ThemeName) => void;
  export let onChangePassword: () => void;
  export let onClose: () => void;

  function usedPercent(total: number, free: number) {
    if (!total) return 0;
    return Math.min(100, Math.round(((total - free) / total) * 100));
  }
</script>

<div class="settings-page">
  <header class="settings-head">
    <button class="icon-btn" type="button" aria-label="返回" on:click={onClose}>
      <Icon name="back" size={22} stroke={2} />
    </button>
    <strong>设置</strong>
  </header>

  <div class="settings-body">
    <section class="settings-group">
      <h3>主题</h3>
      <div class="settings-card">
        {#each THEME_OPTIONS as option (option.id)}
          <button class="theme-row" type="button" on:click={() => onTheme(option.id)}>
            <span class="swatch {option.id}"></span>
            <span class="theme-label">{option.label}</span>
            {#if theme === option.id}
              <span class="check"><Icon name="check" size={20} stroke={2} /></span>
            {/if}
          </button>
        {/each}
      </div>
    </section>

    <section class="settings-group">
      <h3>服务器状态</h3>
      <div class="settings-card">
        {#if info}
          <div class="stat-block">
            <div class="stat-row">
              <span>内存可用</span>
              <b>
                {(info.memory.available_mb / 1024).toFixed(2)} GB /
                {(info.memory.total_mb / 1024).toFixed(2)} GB
              </b>
            </div>
            <div class="stat-bar">
              <i style="width:{usedPercent(info.memory.total_mb, info.memory.available_mb)}%"></i>
            </div>
            <div class="stat-row">
              <span>磁盘可用</span>
              <b>
                {(info.disk.free_mb / 1024).toFixed(1)} GB /
                {(info.disk.total_mb / 1024).toFixed(1)} GB
              </b>
            </div>
            <div class="stat-bar">
              <i style="width:{usedPercent(info.disk.total_mb, info.disk.free_mb)}%"></i>
            </div>
            <div class="stat-row">
              <span>负载</span>
              <b>
                {info.cpu.load_1m.toFixed(2)} / {info.cpu.load_5m.toFixed(2)} /
                {info.cpu.load_15m.toFixed(2)}
              </b>
            </div>
            <div class="stat-row">
              <span>建议并行度</span>
              <b>{info.budgets.recommended_parallelism}</b>
            </div>
          </div>
        {:else}
          <div class="stat-block">
            <div class="stat-row"><span>正在读取服务器状态…</span></div>
          </div>
        {/if}
      </div>
    </section>

    <section class="settings-group">
      <h3>账号</h3>
      <div class="settings-card">
        <button class="settings-row" type="button" on:click={onChangePassword}>
          <span>修改密码</span>
          <Icon name="chevronRight" size={18} />
        </button>
      </div>
    </section>

    <p class="version">remote-agent-lite v0.2.0</p>
  </div>
</div>
