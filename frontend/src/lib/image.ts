/**
 * 一次性聊天图片。
 *
 * 图片只在浏览器里被读成 data URL，随这一轮请求发给 codex app-server，
 * 不经过项目存储、不落盘、不入库；发送成功后对象和 blob URL 即被释放。
 */

export interface PendingImage {
  id: string;
  name: string;
  /** blob: URL，仅用于本地预览，发送后需要释放 */
  previewUrl: string;
  /** data:image/...;base64,...，直接作为一轮输入发给模型 */
  dataUrl: string;
  size: number;
  width: number;
  height: number;
}

export interface ChatImagePayload {
  name: string;
  data_url: string;
}

/** 长边超过这个值就等比缩小；视觉模型再大也吃不到更多信息。 */
const MAX_EDGE = 1568;
const JPEG_QUALITY = 0.85;
/** 已经足够小、又不需要缩放的原图，直接原样发送，避免二次编码掉质量。 */
const PASSTHROUGH_BYTES = 1_500_000;

const SUPPORTED = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp']);
/** canvas 能编码成的格式；bmp 交给 png 承载。 */
const CANVAS_MIME: Record<string, string> = {
  'image/png': 'image/png',
  'image/jpeg': 'image/jpeg',
  'image/webp': 'image/webp',
  'image/bmp': 'image/png'
};

export function normaliseImageType(raw: string): string {
  const type = (raw || '').toLowerCase();
  if (type === 'image/jpg' || type === 'image/pjpeg') return 'image/jpeg';
  return type;
}

export function isSupportedImageFile(file: File): boolean {
  return SUPPORTED.has(normaliseImageType(file.type));
}

export function unsupportedImageMessage(file: File): string {
  const type = normaliseImageType(file.type);
  const name = file.name || '这张图片';
  if (type === 'image/heic' || type === 'image/heif' || /\.(heic|heif)$/i.test(name)) {
    return `${name} 是 HEIC 格式，请先转成 JPEG 再发送`;
  }
  return `${name} 的格式不支持（只支持 png / jpg / gif / webp / bmp）`;
}

function newImageId(): string {
  return `img-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('读取图片失败'));
    reader.readAsDataURL(file);
  });
}

interface ImageSource {
  source: CanvasImageSource;
  width: number;
  height: number;
  release: () => void;
}

async function loadSource(file: File): Promise<ImageSource> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, {
        imageOrientation: 'from-image'
      } as ImageBitmapOptions);
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        release: () => bitmap.close()
      };
    } catch {
      /* 退回 <img> 解码 */
    }
  }
  const url = URL.createObjectURL(file);
  const image = new Image();
  try {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('无法解码这张图片'));
      image.src = url;
    });
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
  return {
    source: image,
    width: image.naturalWidth || image.width,
    height: image.naturalHeight || image.height,
    release: () => URL.revokeObjectURL(url)
  };
}

/** 把用户选择的文件变成可以直接发送的待发图片。 */
export async function prepareImage(file: File): Promise<PendingImage> {
  const type = normaliseImageType(file.type);
  if (!SUPPORTED.has(type)) throw new Error(unsupportedImageMessage(file));

  const previewUrl = URL.createObjectURL(file);
  const base = { id: newImageId(), name: file.name || 'image', previewUrl, size: file.size };

  try {
    if (type === 'image/gif') {
      // 动图不重编码，否则会掉帧。
      const raw = await readAsDataUrl(file);
      return { ...base, dataUrl: raw.replace(/^data:[^;]*/, `data:${type}`), width: 0, height: 0 };
    }

    const loaded = await loadSource(file);
    try {
      const longEdge = Math.max(loaded.width, loaded.height);
      const scale = longEdge > MAX_EDGE ? MAX_EDGE / longEdge : 1;
      if (scale === 1 && file.size <= PASSTHROUGH_BYTES) {
        const raw = await readAsDataUrl(file);
        return {
          ...base,
          dataUrl: raw.replace(/^data:[^;]*/, `data:${type}`),
          width: loaded.width,
          height: loaded.height
        };
      }
      const width = Math.max(1, Math.round(loaded.width * scale));
      const height = Math.max(1, Math.round(loaded.height * scale));
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('当前浏览器无法处理图片');
      context.drawImage(loaded.source, 0, 0, width, height);
      const dataUrl = canvas.toDataURL(CANVAS_MIME[type] || 'image/jpeg', JPEG_QUALITY);
      return { ...base, dataUrl, width, height };
    } finally {
      loaded.release();
    }
  } catch (error) {
    URL.revokeObjectURL(previewUrl);
    throw error;
  }
}

/** 批量处理；单个文件失败不会影响其它文件，错误信息按文件返回。 */
export async function prepareImages(
  files: Iterable<File>
): Promise<{ images: PendingImage[]; errors: string[] }> {
  const images: PendingImage[] = [];
  const errors: string[] = [];
  for (const file of Array.from(files)) {
    try {
      images.push(await prepareImage(file));
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error));
    }
  }
  return { images, errors };
}

export function revokeImage(image: PendingImage): void {
  if (image.previewUrl) URL.revokeObjectURL(image.previewUrl);
}

export function revokeImages(images: Iterable<PendingImage>): void {
  for (const image of images) revokeImage(image);
}
