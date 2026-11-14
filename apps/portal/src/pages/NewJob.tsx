import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { zipSync, unzipSync } from "fflate";
import { api, post, errorText, ApiError } from "../lib/api";
import { upload, excluded } from "../lib/uploads";
import type { Preview, Job } from "../lib/types";
import { UploadPicker } from "../components/UploadPicker";
import { PlanPreview } from "../components/PlanPreview";
import { PageHeader, ErrorBox, bytes } from "../components/Common";
import { Button } from "../components/ui/button";
type Example = {
  id: string;
  name: string;
  description: string;
  requires_gpu: boolean;
};
export function NewJob() {
  const nav = useNavigate();
  const [recipes, setRecipes] = useState<{ id: string; name: string }[]>([]);
  const [examples, setExamples] = useState<Example[]>([]);
  const [recipe, setRecipe] = useState("");
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [project, setProject] = useState<File>();
  const [snippets, setSnippets] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string[]>([]);
  const [consent, setConsent] = useState(false);
  const [preview, setPreview] = useState<Preview>();
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const key = useRef("");
  const keyRevision = useRef("");
  const locked = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    api<typeof recipes>("/recipes")
      .then(setRecipes)
      .catch((e) => setError(errorText(e)));
    api<Example[]>("/examples")
      .then(setExamples)
      .catch((e) => setError(errorText(e)));
  }, []);
  useEffect(() => {
    if (
      !preview ||
      ["ready", "failed", "cancelled", "needs_input"].includes(preview.state)
    )
      return;
    let active = true;
    const timer = setInterval(() => {
      void api<Preview>("/job-previews/" + preview.id)
        .then((p) => {
          if (active)
            setPreview((old) =>
              old && old.plan_revision > p.plan_revision ? old : p,
            );
        })
        .catch((e) => {
          if (active) setError(errorText(e));
        });
    }, 1800);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [preview?.id, preview?.state]);
  function invalidate() {
    generation.current++;
    setPreview(undefined);
    setConsent(false);
    key.current = "";
    setError("");
  }
  async function inspectProject(file: File) {
    const n = ++generation.current;
    setSnippets({});
    setSelected([]);
    setProject(file);
    try {
      if (file.size > 64 * 1024 * 1024)
        throw new Error("Для просмотра проекта выберите ZIP до 64 МБ.");
      let total = 0,
        count = 0;
      const entries = unzipSync(new Uint8Array(await file.arrayBuffer()), {
        filter: (entry) => {
          if (
            excluded(entry.name) ||
            entry.originalSize > 8000 ||
            !/\.(py|txt|md|json|csv|yaml|yml|toml)$/i.test(entry.name)
          )
            return false;
          total += entry.originalSize;
          count++;
          return total <= 24000 && count <= 16;
        },
      });
      const texts: Record<string, string> = {};
      for (const [path, data] of Object.entries(entries)) {
        try {
          texts[path] = new TextDecoder("utf-8", { fatal: true }).decode(data);
        } catch {}
      }
      if (n === generation.current) setSnippets(texts);
    } catch (e) {
      if (n === generation.current) setError(errorText(e));
    }
  }
  async function folder(all: File[]) {
    invalidate();
    setBusy(true);
    try {
      const included = all.filter(
        (f) => !excluded(f.webkitRelativePath || f.name),
      );
      if (included.reduce((n, f) => n + f.size, 0) > 64 * 1024 * 1024)
        throw new Error("Папка проекта должна быть не больше 64 МБ.");
      const contents: Record<string, Uint8Array> = {};
      for (const f of included) {
        const path = f.webkitRelativePath || f.name;
        contents[
          path.includes("/") ? path.slice(path.indexOf("/") + 1) : path
        ] = new Uint8Array(await f.arrayBuffer());
      }
      const zipped = zipSync(contents);
      await inspectProject(
        new File([zipped as BlobPart], "project.zip", {
          type: "application/zip",
        }),
      );
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function prepare() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      const inputs = [];
      let project_artifact_id: string | undefined;
      if (project) {
        project_artifact_id = (
          await upload(
            project,
            (n) =>
              setProgress(
                project.name + ": " + bytes(n) + " / " + bytes(project.size),
              ),
            "project",
          )
        ).id;
        inputs.push({
          artifact_id: project_artifact_id,
          mount_path: "/workspace",
          archive: true,
        });
      }
      for (const f of files.filter(
        (f) => !excluded(f.webkitRelativePath || f.name),
      )) {
        const a = await upload(f, (n) =>
          setProgress(f.name + ": " + bytes(n) + " / " + bytes(f.size)),
        );
        inputs.push({
          artifact_id: a.id,
          mount_path: "/inputs/" + (f.webkitRelativePath || f.name),
          archive: false,
        });
      }
      setPreview(
        await post<Preview>("/job-previews", {
          prompt: prompt.trim(),
          recipe_id: recipe || undefined,
          project_artifact_id,
          inputs,
          selected_files: selected,
          share_with_llm: consent,
        }),
      );
      key.current = "";
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      setProgress("");
      locked.current = false;
    }
  }
  async function run() {
    if (locked.current || !preview || preview.state !== "ready") return;
    locked.current = true;
    setBusy(true);
    setError("");
    const revision = preview.id + ":" + preview.plan_revision;
    if (!key.current || keyRevision.current !== revision) {
      key.current = crypto.randomUUID();
      keyRevision.current = revision;
    }
    try {
      const job = await post<Job>(
        "/jobs",
        { preview_id: preview.id, plan_revision: preview.plan_revision },
        { "Idempotency-Key": key.current },
      );
      nav("/jobs/" + job.id);
    } catch (e) {
      setError(errorText(e));
      if (e instanceof ApiError && e.code === "STALE_PREVIEW") {
        try {
          setPreview(await api<Preview>("/job-previews/" + preview.id));
          key.current = "";
        } catch (next) {
          setError(errorText(next));
          setPreview(undefined);
        }
      }
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader eyebrow="НОВАЯ ЗАДАЧА" title="Что будем вычислять?">
        Опишите результат, добавьте код или данные и проверьте план перед
        запуском.
      </PageHeader>
      {examples.length > 0 && (
        <section className="card">
          <h2>Начните с простого примера</h2>
          <div className="grid">
            {examples.map((ex) => (
              <div key={ex.id}>
                <h3>
                  {ex.requires_gpu ? "Пример на NVIDIA GPU" : "Пример на CPU"}
                </h3>
                <p className="muted">{ex.description}</p>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={async () => {
                    invalidate();
                    setBusy(true);
                    try {
                      setPreview(
                        await post<Preview>(
                          "/examples/" + ex.id + "/preview",
                          {},
                        ),
                      );
                      setError("");
                    } catch (e) {
                      setError(errorText(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Посмотреть план {ex.requires_gpu ? "GPU" : "CPU"}
                </Button>
              </div>
            ))}
          </div>
          <p className="muted">
            Готовый пример не отправляет ваши данные во внешний ИИ. Сначала
            покажем план.
          </p>
        </section>
      )}
      <div className="grid">
        <section className="card">
          <h2>1. Задача и файлы</h2>
          <label>
            Что нужно сделать?
            <textarea
              value={prompt}
              maxLength={8000}
              disabled={busy}
              onChange={(e) => {
                invalidate();
                setPrompt(e.target.value);
              }}
              placeholder="Например: запусти main.py и сохрани результат в result.json"
            />
          </label>
          <label>
            Шаблон задачи
            <select
              value={recipe}
              disabled={busy}
              onChange={(e) => {
                invalidate();
                setRecipe(e.target.value);
              }}
            >
              <option value="">Автоматический выбор по описанию</option>
              {recipes.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <UploadPicker
            files={files}
            disabled={busy}
            onChange={(f) => {
              invalidate();
              setFiles(f);
            }}
          />
          <details>
            <summary>Добавить проект ZIP или папку с кодом</summary>
            <p className="muted">
              Для проекта можно выбрать текстовые файлы, которые увидит ИИ.
              Проверьте ZIP на секреты перед загрузкой.
            </p>
            <label>
              Проект ZIP
              <input
                type="file"
                accept=".zip"
                disabled={busy}
                onChange={(e) => {
                  invalidate();
                  const f = e.target.files?.[0];
                  if (f) void inspectProject(f);
                }}
              />
            </label>
            <label>
              Или папка проекта
              <input
                type="file"
                multiple
                {...{ webkitdirectory: "" }}
                disabled={busy}
                onChange={(e) => void folder(Array.from(e.target.files || []))}
              />
            </label>
            {project && (
              <div>
                <p>
                  {project.name} · {bytes(project.size)}
                </p>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => {
                    invalidate();
                    setProject(undefined);
                    setSnippets({});
                    setSelected([]);
                  }}
                >
                  Убрать проект
                </Button>
              </div>
            )}
          </details>
        </section>
        <section className="card">
          <h2>2. Разрешение на помощь ИИ</h2>
          <p>
            DeepSeek получит описание задачи, сведения о входных файлах и только
            отмеченный ниже текст из проекта. Вычисления выполняются на
            устройствах пула.
          </p>
          {Object.entries(snippets).map(([name, text]) => (
            <details key={name}>
              <summary>{name}</summary>
              <pre>{text}</pre>
              <label className="check">
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={selected.includes(name)}
                  onChange={(e) => {
                    invalidate();
                    setSelected(
                      e.target.checked
                        ? [...selected, name]
                        : selected.filter((n) => n !== name),
                    );
                  }}
                />
                Отправить текст этого файла в ИИ
              </label>
            </details>
          ))}
          <p className="muted">
            Файлы не выбраны автоматически. Для ZIP показаны до 16 текстовых
            файлов размером до 8 КБ каждый, суммарно до 24 КБ. Обычный код и
            данные загружаются в пул, их содержимое не отправляется в ИИ без
            выбора в проекте.
          </p>
          <label className="check">
            <input
              type="checkbox"
              checked={consent}
              disabled={busy}
              onChange={(e) => setConsent(e.target.checked)}
            />
            Разрешаю отправить описание и выбранный текст в DeepSeek
          </label>
          <Button
            disabled={busy || !consent || !prompt.trim()}
            onClick={() => void prepare()}
          >
            {busy ? "Подготавливаем…" : "Подготовить план"}
          </Button>
          <p aria-live="polite">{progress}</p>
        </section>
      </div>
      <ErrorBox message={error} />
      {preview && (
        <>
          <PlanPreview plan={preview} />
          {preview.state === "needs_input" && preview.question && (
            <form
              className="card"
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                try {
                  setPreview(
                    await post<Preview>(
                      "/job-previews/" + preview.id + "/answers",
                      {
                        question_id: preview.question!.question_id,
                        plan_revision: preview.plan_revision,
                        answer,
                      },
                    ),
                  );
                  setAnswer("");
                  setError("");
                } catch (e) {
                  setError(errorText(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                {preview.question.prompt}
                <input
                  required
                  maxLength={2000}
                  value={answer}
                  disabled={busy}
                  onChange={(e) => setAnswer(e.target.value)}
                />
              </label>
              <Button disabled={busy}>Отправить ответ</Button>
            </form>
          )}
          <div className="actions">
            <Button
              disabled={busy || preview.state !== "ready" || !preview.plan}
              onClick={() => void run()}
            >
              Запустить задачу
            </Button>
            {!["failed", "cancelled"].includes(preview.state) && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    setPreview(
                      await post<Preview>(
                        "/job-previews/" + preview.id + "/cancel",
                        {},
                      ),
                    );
                  } catch (e) {
                    setError(errorText(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Отменить план
              </Button>
            )}
            <small>Запускается только проверенная версия плана.</small>
          </div>
        </>
      )}
    </>
  );
}
