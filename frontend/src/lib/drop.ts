/**
 * 把「拖进面板的东西」变成待上传的文件列表。
 *
 * 拖进来的可能是一整个文件夹，`dataTransfer.files` 只会给出顶层目录里的文件，
 * 所以这里优先走 `webkitGetAsEntry()` 自己递归目录树；拿不到条目时退回普通文件列表。
 * 注意：`DataTransferItem` 只在 drop 事件里有效，所以条目必须先同步取出来再 await。
 */

export interface DroppedFile {
  file: File;
  /** 相对路径（文件夹拖入时非空），普通文件是空串。 */
  path: string;
}

function readAllEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const all: FileSystemEntry[] = [];
    const step = () => {
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(all);
          return;
        }
        all.push(...batch);
        step();
      }, reject);
    };
    step();
  });
}

async function walk(entry: FileSystemEntry, prefix: string, out: DroppedFile[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => {
      (entry as FileSystemFileEntry).file(resolve, reject);
    });
    out.push({ file, path: `${prefix}${entry.name}` });
    return;
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (const child of await readAllEntries(reader)) {
      await walk(child, `${prefix}${entry.name}/`, out);
    }
  }
}

export async function collectDropped(dataTransfer: DataTransfer | null): Promise<DroppedFile[]> {
  if (!dataTransfer) return [];
  const items = Array.from(dataTransfer.items || []).filter((item) => item.kind === 'file');
  // 必须先同步取条目：等过一次 await，条目就失效了
  const entries = items
    .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
    .filter((entry): entry is FileSystemEntry => Boolean(entry));

  if (!entries.length) {
    return Array.from(dataTransfer.files || []).map((file) => ({ file, path: '' }));
  }
  const out: DroppedFile[] = [];
  for (const entry of entries) await walk(entry, '', out);
  return out;
}
