import { api, post, ApiError } from "./api";
import type { Artifact } from "./types";
export function excluded(path: string) {
  return path
    .replaceAll("\\", "/")
    .split("/")
    .some((segment) => {
      const n = segment.toLowerCase();
      return (
        [
          ".git",
          "node_modules",
          ".ssh",
          ".aws",
          ".azure",
          ".npmrc",
          ".pypirc",
          "id_rsa",
          "id_ed25519",
        ].includes(n) ||
        n === ".env" ||
        n.startsWith(".env.") ||
        /\.(pem|key)$/i.test(n)
      );
    });
}
async function sha(blob: Blob) {
  const data = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(data))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export async function upload(
  file: File,
  progress: (n: number) => void,
  kind: "input" | "project" = "input",
): Promise<Artifact> {
  const path = file.webkitRelativePath || file.name;
  const digest = await sha(file);
  const key = "campus-upload:" + kind + ":" + path + ":" + digest;
  let id = sessionStorage.getItem(key);
  if (!id) {
    const created = await post<{ id: string }>("/uploads", {
      name: path,
      size_bytes: file.size,
      sha256: digest,
      media_type: file.type || "application/octet-stream",
      kind,
    });
    id = created.id;
    sessionStorage.setItem(key, id);
  }
  let status: {
    state: string;
    part_size_bytes: number;
    parts: { number: number; sha256: string; size_bytes: number }[];
    artifact: Artifact | null;
  };
  try {
    status = await api("/uploads/" + id);
  } catch (e) {
    if (
      e instanceof ApiError &&
      ["404", "NOT_FOUND", "UPLOAD_EXPIRED"].includes(e.code)
    )
      sessionStorage.removeItem(key);
    throw e;
  }
  if (status.state === "expired") {
    sessionStorage.removeItem(key);
    throw new Error(
      "Срок загрузки истёк. Нажмите «Подготовить план», чтобы начать снова.",
    );
  }
  if (status.artifact) {
    sessionStorage.removeItem(key);
    return status.artifact;
  }
  const size = status.part_size_bytes;
  if (!Number.isSafeInteger(size) || size <= 0)
    throw new Error("Сервер вернул неверный размер части файла.");
  for (let offset = 0, part = 0; offset < file.size; offset += size, part++) {
    const blob = file.slice(offset, offset + size),
      hash = await sha(blob),
      recorded = status.parts.find((p) => p.number === part);
    if (recorded && recorded.sha256 !== hash) {
      sessionStorage.removeItem(key);
      throw new Error("Файл изменился. Повторите загрузку.");
    }
    if (!recorded)
      await api("/uploads/" + id + "/parts/" + part, {
        method: "PUT",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Content-SHA256": hash,
        },
        body: blob,
      });
    progress(Math.min(file.size, offset + size));
  }
  const artifact = await post<Artifact>("/uploads/" + id + "/finalize", {});
  sessionStorage.removeItem(key);
  return artifact;
}
