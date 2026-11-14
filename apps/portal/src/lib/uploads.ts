import { api, post } from "./api";
export function excluded(path: string) {
  return path
    .split("/")
    .some(
      (p) =>
        p === ".git" ||
        p === "node_modules" ||
        p === ".env" ||
        p.startsWith(".env.") ||
        /\.(pem|key)$/i.test(p),
    );
}
export async function upload(file: File, progress: (n: number) => void) {
  const path = file.webkitRelativePath || file.name;
  const key =
    "campus-upload:" + path + ":" + file.size + ":" + file.lastModified;
  let id = sessionStorage.getItem(key);
  if (!id) {
    const created = await post<{ id: string }>("/uploads", {
      name: path,
      size_bytes: file.size,
    });
    id = created.id;
    sessionStorage.setItem(key, id);
  }
  const status = await api<{
    part_size_bytes: number;
    uploaded_parts: number[];
  }>("/uploads/" + id);
  const partSize = status.part_size_bytes;
  if (!Number.isSafeInteger(partSize) || partSize <= 0)
    throw new Error("Invalid upload part size");
  for (
    let offset = 0, part = 0;
    offset < file.size;
    offset += partSize, part++
  ) {
    const blob = file.slice(offset, offset + partSize);
    if (!status.uploaded_parts.includes(part)) {
      const digest = await crypto.subtle.digest(
        "SHA-256",
        await blob.arrayBuffer(),
      );
      const hex = Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      await api("/uploads/" + id + "/parts/" + part, {
        method: "PUT",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Content-SHA256": hex,
        },
        body: blob,
      });
    }
    progress(Math.min(file.size, offset + partSize));
  }
  const result = await post<{ artifact_id: string }>(
    "/uploads/" + id + "/finalize",
    {},
  );
  sessionStorage.removeItem(key);
  return result.artifact_id;
}
