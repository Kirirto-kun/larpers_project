import type { ChangeEvent } from "react";
import { excluded } from "../lib/uploads";
import { bytes } from "./Common";
export function UploadPicker({
  files,
  onChange,
}: {
  files: File[];
  onChange: (files: File[]) => void;
}) {
  const pick = (e: ChangeEvent<HTMLInputElement>) =>
    onChange(Array.from(e.target.files || []));
  return (
    <>
      <label>
        Project or dataset files
        <input type="file" multiple onChange={pick} />
      </label>
      <label>
        Or select a project folder
        <input
          type="file"
          multiple
          {...{ webkitdirectory: "" }}
          onChange={pick}
        />
      </label>
      <small>
        ZIP projects are inspected by the server before execution. Sensitive
        files inside archives must also be excluded server-side.
      </small>
      {files.map((f, i) => (
        <div className="row" key={i}>
          <span>
            {f.webkitRelativePath || f.name}
            {excluded(f.webkitRelativePath || f.name) && (
              <b> · Excluded (secret or dependency)</b>
            )}
          </span>
          <small>{bytes(f.size)}</small>
        </div>
      ))}
      <p className="muted">
        {files.filter((f) => !excluded(f.webkitRelativePath || f.name)).length}{" "}
        included files ·{" "}
        {bytes(
          files
            .filter((f) => !excluded(f.webkitRelativePath || f.name))
            .reduce((n, f) => n + f.size, 0),
        )}
      </p>
    </>
  );
}
