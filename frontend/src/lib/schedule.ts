/** 定时任务的时间文案：列表第二行、以及新建表单里的提示。 */

import type { ScheduledTask } from './types';

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** 把 ISO 时间转成 `MM-DD HH:mm`（按本机时区显示，输入时按服务器本地时间解释）。 */
export function formatStamp(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes()
  )}`;
}

function humanize(ms: number): string {
  if (ms <= 0) return '即将执行';
  if (ms < MINUTE) return '不到 1 分钟';
  if (ms < HOUR) return `${Math.round(ms / MINUTE)} 分钟后`;
  if (ms < DAY) return `${Math.round(ms / HOUR)} 小时后`;
  if (ms < 30 * DAY) return `${Math.round(ms / DAY)} 天后`;
  return `${Math.round(ms / (30 * DAY))} 个月后`;
}

/** `每隔 6 小时` / `每隔 2 天` / `每隔 90 分钟`。 */
export function intervalLabel(seconds: number): string {
  if (seconds % DAY === 0) return `每隔 ${seconds / DAY} 天`;
  if (seconds % HOUR === 0) return `每隔 ${seconds / HOUR} 小时`;
  return `每隔 ${Math.round(seconds / 60)} 分钟`;
}

/** 列表第二行：`每隔 6 小时 · 下次 2 小时后` / `一次性 · 09-25 09:00`。 */
export function taskWhenLabel(task: ScheduledTask, now = Date.now()): string {
  const next = new Date(task.next_run_at).getTime();
  if (task.kind === 'once') {
    const suffix = Number.isNaN(next) ? '' : ` · ${humanize(next - now)}`;
    return `一次性 · ${formatStamp(task.next_run_at)}${suffix}`;
  }
  const every = intervalLabel(Number(task.interval_seconds || 0));
  if (Number.isNaN(next)) return every;
  return `${every} · 下次 ${humanize(next - now)}`;
}

/** 新建表单：把 `2026-09-25T09:00` 这样的本地时间串转成 Date（浏览器按本地时区解析）。 */
export function parseLocalInput(value: string): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** `datetime-local` 输入框的默认值：一小时后，取整到 5 分钟。 */
export function defaultRunAt(now = new Date()): string {
  const date = new Date(now.getTime() + HOUR);
  date.setMinutes(Math.ceil(date.getMinutes() / 5) * 5, 0, 0);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}
