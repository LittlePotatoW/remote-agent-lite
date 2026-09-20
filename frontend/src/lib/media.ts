const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];

const EXTERNAL_SRC = /^(https?:|data:|blob:)/i;

export function isImagePath(path: string): boolean {
  const clean = (path || '').split(/[?#]/)[0];
  const dot = clean.lastIndexOf('.');
  if (dot === -1) return false;
  return IMAGE_EXTENSIONS.includes(clean.slice(dot + 1).toLowerCase());
}

export function rawImageUrl(projectId: string, path: string): string {
  return `/api/projects/${projectId}/files/raw?path=${encodeURIComponent(path)}`;
}

export function downloadUrl(projectId: string, path: string): string {
  return `/api/projects/${projectId}/files/download?path=${encodeURIComponent(path)}`;
}

/**
 * 把各种写法（相对路径、/srv/... 绝对路径、file:// 前缀）归一化成项目内相对路径。
 * 外链、data: 返回 null。
 */
export function localProjectPath(src: string): string | null {
  const value = (src || '').trim();
  if (!value || EXTERNAL_SRC.test(value)) return null;

  let path = value.startsWith('file://') ? value.slice('file://'.length) : value;
  path = path.replace(/\\/g, '/');

  const marker = '/projects/';
  const markerIndex = path.indexOf(marker);
  if (path.startsWith('/') && markerIndex !== -1) {
    const rest = path.slice(markerIndex + marker.length);
    const slash = rest.indexOf('/');
    path = slash === -1 ? '' : rest.slice(slash + 1);
  } else {
    path = path.replace(/^\/+/, '');
  }
  return path || null;
}

/**
 * 把回复里指向项目文件的图片地址，改写成后端的原图接口地址。
 * 外链、data: 原样保留；其它类型或无法解析的返回 null。
 */
export function mapImageSrc(src: string, projectId: string): string | null {
  const value = (src || '').trim();
  if (!value) return null;
  if (EXTERNAL_SRC.test(value)) return value;
  const path = localProjectPath(value);
  if (!path || !isImagePath(path)) return null;
  return rawImageUrl(projectId, path);
}
