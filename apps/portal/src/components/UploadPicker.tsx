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
        Код и данные
        <input type="file" multiple disabled={disabled} onChange={pick} />
      </label>
      {files.map((f, i) => (
        <div className="row" key={i}>
          <span>
            {f.webkitRelativePath || f.name}
            {excluded(f.webkitRelativePath || f.name) && (
              <b> · Исключён: секрет или служебный файл</b>
            )}
          </span>
          <small>{bytes(f.size)}</small>
        </div>
      ))}
      <p className="muted">
        Файлы кода и данные загрузятся в пул. .env, ключи и служебные папки
        исключаются. Проверьте содержимое перед загрузкой.
      </p>
    </>
  );
}
