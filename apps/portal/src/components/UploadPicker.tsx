import type { ChangeEvent } from "react";
import { excluded } from "../lib/uploads";
import { bytes } from "./Common";
export function UploadPicker({
  files,
  onChange,
  disabled = false,
}: {
  files: File[];
  onChange: (files: File[]) => void;
  disabled?: boolean;
}) {
  const pick = (e: ChangeEvent<HTMLInputElement>) =>
    onChange(Array.from(e.target.files || []));
  return (
    <>
      <label>
        Code and data
        <input type="file" multiple disabled={disabled} onChange={pick} />
      </label>
      {files.map((f, i) => (
        <div className="row" key={i}>
          <span>
            {f.webkitRelativePath || f.name}
            {excluded(f.webkitRelativePath || f.name) && (
              <b> · Excluded: secret or system file</b>
            )}
          </span>
          <small>{bytes(f.size)}</small>
        </div>
      ))}
      <p className="muted">
        Files are uploaded to the pool. .env files, keys, and system folders are
        excluded. Review your files before uploading.
      </p>
    </>
  );
}
