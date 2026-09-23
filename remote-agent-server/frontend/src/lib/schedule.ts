/** 定时任务的时间文案（列表第二行）和新建表单用的滚轮选项。 */

import type { ScheduledTask } from './types';

export interface WheelOption {
  value: number;
  label: string;
}

/** 0 = 周日 … 6 = 周六，和后端以及 `Date#getDay()` 一致。 */
export const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

const pad = (n: number) => String(n).padStart(2, '0');

const range = (from: number, to: number): number[] =>
  Array.from({ length: to - from + 1 }, (_, index) => from + index);

export const MONTH_OPTIONS: WheelOption[] = range(1, 12).map((value) => ({
  value,
  label: String(value)
}));

export const HOUR_OPTIONS: WheelOption[] = range(0, 23).map((value) => ({
  value,
  label: String(value)
}));

export const MINUTE_OPTIONS: WheelOption[] = range(0, 59).map((value) => ({
  value,
  label: pad(value)
}));

/** 「日」滚轮：定时模式跟着月份走（2 月给到 29），循环模式固定 1–31。 */
export function dayOptions(month?: number | null): WheelOption[] {
  const limit = month ? new Date(2024, month, 0).getDate() : 31;
  return range(1, limit).map((value) => ({ value, label: String(value) }));
}

/** 列表第二行：`今天 23:30` / `每天 09:00` / `每周五 18:00` / `每月 1 日 09:00`。 */
export function taskWhenLabel(task: ScheduledTask, now = new Date()): string {
  const time = `${pad(Number(task.hour) || 0)}:${pad(Number(task.minute) || 0)}`;
  if (task.kind === 'daily') return `每天 ${time}`;
  if (task.kind === 'weekly') return `每${WEEKDAYS[Number(task.weekday) || 0]} ${time}`;
  if (task.kind === 'monthly') return `每月 ${Number(task.day) || 1} 日 ${time}`;

  const next = new Date(task.next_run_at);
  if (Number.isNaN(next.getTime())) return `定时 ${time}`;
  const clock = `${pad(next.getHours())}:${pad(next.getMinutes())}`;
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  if (sameDay(next, now)) return `今天 ${clock}`;
  if (sameDay(next, new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1))) {
    return `明天 ${clock}`;
  }
  const day = `${next.getMonth() + 1} 月 ${next.getDate()} 日`;
  return next.getFullYear() === now.getFullYear()
    ? `${day} ${clock}`
    : `${next.getFullYear()} 年 ${day} ${clock}`;
}

/** 新建表单默认值：一次性给「一小时后（取整到 5 分钟）」。 */
export function defaultOnce(now = new Date()): { month: number; day: number; hour: number; minute: number } {
  const soon = new Date(now.getTime() + 60 * 60 * 1000);
  soon.setMinutes(Math.ceil(soon.getMinutes() / 5) * 5, 0, 0);
  return {
    month: soon.getMonth() + 1,
    day: soon.getDate(),
    hour: soon.getHours(),
    minute: soon.getMinutes()
  };
}

/** 新建表单默认值：循环给「09:00」，星期/日期跟着今天。 */
export function defaultLoop(now = new Date()): { hour: number; minute: number; weekday: number; day: number } {
  return { hour: 9, minute: 0, weekday: now.getDay(), day: now.getDate() };
}
