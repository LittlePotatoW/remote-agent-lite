import type { ThemeName } from './types';

const STORAGE_KEY = 'ral-theme';
const THEMES: ThemeName[] = ['blue', 'mono', 'orange'];

export const THEME_OPTIONS: { id: ThemeName; label: string }[] = [
  { id: 'blue', label: '蓝色系' },
  { id: 'mono', label: '黑色系' },
  { id: 'orange', label: '橙色系' }
];

export function readTheme(): ThemeName {
  try {
    const stored = localStorage.getItem(STORAGE_KEY) as ThemeName | null;
    if (stored && THEMES.includes(stored)) return stored;
  } catch {
    /* 隐私模式下拿不到 localStorage，用默认主题 */
  }
  return 'blue';
}

export function applyTheme(name: ThemeName): void {
  document.documentElement.dataset.theme = name;
  try {
    localStorage.setItem(STORAGE_KEY, name);
  } catch {
    /* 存不了就只在本次会话生效 */
  }
}
