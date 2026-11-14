import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, post, errorText } from "../lib/api";
import { subscribe } from "../lib/events";
import { upload, excluded } from "../lib/uploads";
import type { Preview, Job } from "../lib/types";
import { UploadPicker } from "../components/UploadPicker";
import { PlanPreview } from "../components/PlanPreview";
import { PageHeader, ErrorBox } from "../components/Common";
import { Button } from "../components/ui/button";
export function NewJob() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [recipes, setRecipes] = useState<{ id: string; name: string }[]>([]);
  const [recipe, setRecipe] = useState(params.get("recipe") || "");
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [fragments, setFragments] = useState<Record<string, string>>({});
  const [consent, setConsent] = useState(false);
  const [preview, setPreview] = useState<Preview>();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const key = useRef("");
  const submittedRevision = useRef("");
  const lock = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    api<typeof recipes>("/recipes")
      .then(setRecipes)
      .catch((e) => setError(errorText(e)));
  }, []);
  useEffect(() => {
    if (!preview) return;
    const id = preview.id;
    let live = true;
    const refresh = () => {
      void api<Preview>("/job-previews/" + id)
        .then((p) => {
          if (live)
            setPreview((previous) =>
              previous &&
              previous.id === p.id &&
              previous.plan_revision > p.plan_revision
                ? previous
                : p,
            );
        })
        .catch((e) => {
          if (live) setError(errorText(e));
        });
    };
    const stop = subscribe(
      "/job-previews/" + id + "/events",
      refresh,
      () => {},
    );
    return () => {
      live = false;
      stop();
    };
  }, [preview?.id]);
  function invalidate() {
    generation.current++;
    setPreview(undefined);
    key.current = "";
    setConsent(false);
  }
  async function pick(next: File[]) {
    invalidate();
    setFiles(next);
    setSelected({});
    setFragments({});
    const current = generation.current;
    const entries = await Promise.all(
      next
        .filter(
          (f) =>
            !excluded(f.webkitRelativePath || f.name) &&
            /\.(txt|md|py|json|csv)$/i.test(f.name),
        )
        .map(
          async (f) =>
            [
              f.webkitRelativePath || f.name,
              await f.slice(0, 4096).text(),
            ] as const,
        ),
    );
    if (current === generation.current)
      setFragments(Object.fromEntries(entries));
  }
  async function prepare() {
    setBusy(true);
    setError("");
    try {
      const artifact_ids = [];
      for (const f of files.filter(
        (f) => !excluded(f.webkitRelativePath || f.name),
      )) {
        artifact_ids.push(
          await upload(f, (n) =>
            setProgress(`${f.name}: ${n} / ${f.size} bytes`),
          ),
        );
      }
      const p = await post<Preview>("/job-previews", {
        prompt,
        recipe_id: recipe || undefined,
        artifact_ids,
        llm_fragments: Object.entries(fragments)
          .filter(([name]) => selected[name])
          .map(([path, text]) => ({ path, text })),
      });
      setPreview(p);
      key.current = crypto.randomUUID();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      setProgress("");
    }
  }
  async function run() {
    if (lock.current || !preview || preview.state !== "ready") return;
    lock.current = true;
    setBusy(true);
    try {
      const revision = `${preview.id}:${preview.plan_revision}`;
      if (!key.current || submittedRevision.current !== revision) {
        key.current = crypto.randomUUID();
        submittedRevision.current = revision;
      }
      const job = await post<Job>(
        "/jobs",
        { preview_id: preview.id, plan_revision: preview.plan_revision },
        { "Idempotency-Key": key.current },
      );
      nav("/jobs/" + job.id);
    } catch (e) {
      setError(errorText(e));
      if ((e as { code?: string }).code === "STALE_PREVIEW") {
        try {
          const current = await api<Preview>("/job-previews/" + preview.id);
          key.current = crypto.randomUUID();
          setPreview(current);
        } catch (refreshError) {
          setError(errorText(refreshError));
          setPreview(undefined);
        }
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader eyebrow="NEW TASK" title="What would you like to work on?">
        Describe the outcome, choose your inputs, then review the plan before
        anything runs.
      </PageHeader>
      <div className="grid">
        <section className="card">
          <h2>1. Describe your task</h2>
          <label>
            Request
            <textarea
              placeholder="Find passages about battery recycling / 查找电池回收资料"
              value={prompt}
              onChange={(e) => {
                invalidate();
                setPrompt(e.target.value);
              }}
              disabled={busy}
            />
          </label>
          <label>
            Recipe
            <select
              value={recipe}
              onChange={(e) => {
                invalidate();
                setRecipe(e.target.value);
              }}
              disabled={busy}
            >
              <option value="">Choose a recipe or bring your code</option>
              {recipes.map((r) => (
                <option value={r.id} key={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <h2>2. Choose your inputs</h2>
          <UploadPicker
            files={files}
            onChange={(next) => {
              if (!busy) void pick(next);
            }}
          />
        </section>
        <section className="card">
          <h2>3. Review external AI disclosure</h2>
          <p>
            Inputs and computation stay in the private pool. Your request,
            recipe selection, and only the text fragments you select below are
            sent to the external planning API.
          </p>
          {Object.entries(fragments).map(([name, text]) => (
            <details key={name}>
              <summary>{name}</summary>
              <pre>{text}</pre>
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!selected[name]}
                  onChange={(e) => {
                    invalidate();
                    setSelected({ ...selected, [name]: e.target.checked });
                  }}
                />
                Send this fragment to the planner
              </label>
            </details>
          ))}
          <p className="muted">
            First 4 KiB of each supported text file are shown. No file fragments
            selected by default. Review the text for secrets before sharing.
          </p>
          <label className="check">
            <input
              type="checkbox"
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              disabled={busy}
            />
            I reviewed the files and external AI disclosure.
          </label>
          <Button
            disabled={busy || !consent || !prompt.trim()}
            onClick={() => void prepare()}
          >
            {busy ? "Preparing…" : "Prepare execution plan"}
          </Button>
          <p aria-live="polite">{progress}</p>
          <ErrorBox message={error} />
        </section>
      </div>
      {preview && (
        <>
          <PlanPreview plan={preview} />
          {preview.questions.map((q) => (
            <form
              className="card"
              key={q.question_id}
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                try {
                  setPreview(
                    await post<Preview>(
                      "/job-previews/" + preview.id + "/answers",
                      {
                        question_id: q.question_id,
                        plan_revision: preview.plan_revision,
                        answer: answers[q.question_id],
                      },
                    ),
                  );
                } catch (e) {
                  setError(errorText(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                {q.message}
                <input
                  required
                  value={answers[q.question_id] || ""}
                  onChange={(e) =>
                    setAnswers({ ...answers, [q.question_id]: e.target.value })
                  }
                />
              </label>
              <Button disabled={busy}>Send answer</Button>
            </form>
          ))}
          <div className="actions">
            <Button
              disabled={busy || preview.state !== "ready"}
              onClick={() => void run()}
            >
              Run task
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={async () => {
                try {
                  await post("/job-previews/" + preview.id + "/cancel", {});
                  setPreview(await api<Preview>("/job-previews/" + preview.id));
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              Cancel planning
            </Button>
            <small>Only the current, validated plan can run.</small>
          </div>
        </>
      )}
    </>
  );
}
