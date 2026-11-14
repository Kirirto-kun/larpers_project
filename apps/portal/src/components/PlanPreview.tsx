import type { Preview } from "../lib/types";
import { Status, bytes, ErrorBox, date } from "./Common";
export function PlanPreview({ plan: preview }: { plan: Preview }) {
  return (
    <section className="card preview-card">
      <div className="row">
        <h2>Проверьте план</h2>
        <Status value={preview.state} />
      </div>
      <ErrorBox message={preview.error_message || ""} />
      {!preview.plan && (
        <p>
          {preview.state === "needs_input"
            ? "Ответьте на вопрос ниже, чтобы продолжить."
            : preview.state === "failed"
              ? "Измените запрос или попробуйте подготовить план снова."
              : "Готовим и проверяем план. Вычисления ещё не запущены."}
        </p>
      )}
      {preview.plan && (
        <>
          <p className="muted">
            Запуск начнётся после вашего подтверждения. Если подходящих
            устройств нет, задача будет ожидать их подключения.
          </p>
          {preview.plan.stages.map((s, i) => (
            <article className="plan-stage" key={s.stage_id}>
              <h3>
                Шаг {i + 1} · {s.stage_id}
              </h3>
              <dl>
                <dt>Вычисления</dt>
                <dd>
                  {[
                    ...new Set(
                      s.variants.map((v) =>
                        v.accelerator === "cuda" ? "NVIDIA CUDA" : "CPU",
                      ),
                    ),
                  ].join(" / ")}
                </dd>
                <dt>Лимит времени шага</dt>
                <dd>{Math.round(s.task_deadline_ms / 1000)} сек.</dd>
                <dt>Ресурсы</dt>
                <dd>
                  {s.variants.map((v, k) => (
                    <div key={k}>
                      {(v.resource_requirements.cpu_millis / 1000).toFixed(1)}{" "}
                      CPU · {bytes(v.resource_requirements.memory_bytes)} памяти
                      {v.resource_requirements.gpu_slots > 0 ? " · GPU" : ""}
                    </div>
                  ))}
                </dd>
                <dt>Результаты</dt>
                <dd>{s.outputs.map((o) => o.path).join(", ")}</dd>
                <dt>При прерывании</dt>
                <dd>
                  {s.resume_policy === "checkpoint"
                    ? "Продолжение с контрольной точки"
                    : s.resume_policy === "restart"
                      ? "Повторный запуск"
                      : "Возобновление не поддерживается"}
                </dd>
              </dl>
              <details>
                <summary>Команда и окружение</summary>
                <code>{s.argv.join(" ")}</code>
                <p>
                  {s.adapter.id} · {s.adapter.version}
                </p>
              </details>
            </article>
          ))}
          <p>Общий срок выполнения: {date(preview.plan.job_deadline_at)}</p>
          <details>
            <summary>Допущения и основания плана</summary>
            <ul>
              {preview.plan.assumptions.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
              {preview.plan.evidence.map((e, i) => (
                <li key={"e" + i}>
                  {e.source}: {e.detail}
                </li>
              ))}
            </ul>
          </details>
          <small>Версия плана: {preview.plan_revision}</small>
          <details>
            <summary>Данные плана для измерений</summary>
            <p>
              ID: <code>{preview.id}</code>
            </p>
            <p>Версия: {preview.plan_revision}</p>
          </details>
        </>
      )}
    </section>
  );
}
