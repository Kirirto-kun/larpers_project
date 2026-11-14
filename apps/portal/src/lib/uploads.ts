import { api, post, ApiError } from "./api";
export function excluded(path: string) {
  return path
    .replaceAll("\\", "/")
    .split("/")
    .some((segment) => {
      const name = segment.toLowerCase();
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
        ].includes(name) ||
        name === ".env" ||
        name.startsWith(".env.") ||
        /\.(pem|key)$/i.test(name)
      );
    });
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
  let status: {
    part_size_bytes: number;
    uploaded_parts: number[];
    part_checksums?: Record<string, string>;
  };
  try {
    status = await api<{
      part_size_bytes: number;
      uploaded_parts: number[];
      part_checksums?: Record<string, string>;
    }>("/uploads/" + id);
  } catch (error) {
    if (
      error instanceof ApiError &&
      ["404", "NOT_FOUND", "UPLOAD_EXPIRED"].includes(error.code)
    )
      sessionStorage.removeItem(key);
    throw error;
  }
  const partSize = status.part_size_bytes;
  if (!Number.isSafeInteger(partSize) || partSize <= 0)
    throw new Error("Invalid upload part size");
  for (
    let offset = 0, part = 0;
    offset < file.size;
    offset += partSize, part++
  ) {
    const blob = file.slice(offset, offset + partSize);
    {
      const digest = await crypto.subtle.digest(
        "SHA-256",
        await blob.arrayBuffer(),
      );
      const hex = Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      const recorded = status.part_checksums?.[String(part)];
      if (recorded && recorded !== hex) {
        sessionStorage.removeItem(key);
        throw new Error(
          "Selected file differs from the interrupted upload. Retry to create a new upload.",
        );
      }
      // Without a server checksum, re-send the part idempotently rather than trusting metadata alone.
      if (!status.uploaded_parts.includes(part) || recorded !== hex)
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
