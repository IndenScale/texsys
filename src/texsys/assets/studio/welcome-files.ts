// Browser-granted handles belong to the welcome workspace, outside the Studio project's API.
export type LocalFolder = FileSystemDirectoryHandle & {
  values(): AsyncIterable<LocalEntry>;
};
export type LocalEntry = LocalFolder | FileSystemFileHandle;
type Access = {
  requestPermission(options: { mode: "read" | "readwrite" }): Promise<PermissionState>;
};

export const localPickers = window as Window & {
  showDirectoryPicker?(options: { mode: "read" }): Promise<LocalFolder>;
  showOpenFilePicker?(): Promise<FileSystemFileHandle[]>;
  showSaveFilePicker?(options: { suggestedName: string }): Promise<FileSystemFileHandle>;
};

export async function requestFileAccess(entry: LocalEntry, mode: "read" | "readwrite") {
  return (await (entry as LocalEntry & Access).requestPermission({ mode })) === "granted";
}

export async function readLocalText(handle: FileSystemFileHandle) {
  const file = await handle.getFile();
  if (file.size > 2_000_000) throw new Error("只能打开不超过 2 MB 的 UTF-8 文本文件。");
  const bytes = await file.arrayBuffer();
  try {
    const value = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
    if (value.includes("\0")) throw new Error();
    return value;
  } catch {
    throw new Error("只能打开不超过 2 MB 的 UTF-8 文本文件。");
  }
}

export async function listLocalFolder(folder: LocalFolder) {
  const entries: LocalEntry[] = [];
  for await (const entry of folder.values()) entries.push(entry);
  return entries.sort((a, b) =>
    a.kind === b.kind
      ? a.name.localeCompare(b.name, undefined, { numeric: true })
      : a.kind === "directory"
        ? -1
        : 1,
  );
}

export type RecentLocalEntry = { id: string; handle: LocalEntry };
const recentKey = "recent";

async function recentStorage<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
) {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open("tex-studio-welcome", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("handles");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction("handles", mode);
      const request = run(tx.objectStore("handles"));
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export async function readLocalRecents(): Promise<RecentLocalEntry[]> {
  return (await recentStorage("readonly", (store) => store.get(recentKey))) ?? [];
}

export async function rememberLocalEntry(handle: LocalEntry): Promise<RecentLocalEntry[]> {
  const previous = await readLocalRecents();
  const same = await Promise.all(
    previous.map((item) => item.handle.isSameEntry(handle).catch(() => false)),
  );
  const existing = previous.find((_, index) => same[index]);
  const next = [
    { id: existing?.id ?? crypto.randomUUID(), handle },
    ...previous.filter((_, index) => !same[index]),
  ].slice(0, 8);
  await recentStorage("readwrite", (store) => store.put(next, recentKey));
  return next;
}

export async function forgetLocalEntry(id: string): Promise<RecentLocalEntry[]> {
  const next = (await readLocalRecents()).filter((item) => item.id !== id);
  await recentStorage("readwrite", (store) => store.put(next, recentKey));
  return next;
}
