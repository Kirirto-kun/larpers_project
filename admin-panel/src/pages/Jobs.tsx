import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { useResource } from "../lib/hooks";
import type { Job } from "../lib/types";
import { PageHeader, Status, ErrorBox, Empty } from "../components/Common";
import { AttemptTimeline } from "../components/AttemptTimeline";
export function Jobs() {
  const { data, error } = useResource<Job[]>("/jobs", "/pool/events");
  const [selected, setSelected] = useState("");
  const job = data?.find((j) => j.id === selected);
  return (
    <div className="workspace-page">
      <PageHeader eyebrow="" title="Jobs" />
      <ErrorBox message={error} />
      <section className="workspace-section">
        {data?.length === 0 && <Empty>No jobs yet.</Empty>}
        {data?.map((j) => (
          <div className="row job-row" key={j.id}>
            <div>
              <button
                className="item-button"
                aria-expanded={selected === j.id}
                onClick={() => setSelected(selected === j.id ? "" : j.id)}
              >
                {j.name}
                <ChevronRight aria-hidden="true" />
              </button>
              <p>
                {j.accepted_units}/{j.total_units} accepted · {j.failed_units}{" "}
                failed
              </p>
            </div>
            <div className="job-completion">
              {j.total_units > 0 ? (
                <>
                  <div
                    className="visual-track"
                    role="progressbar"
                    aria-label={j.name + " accepted units"}
                    aria-valuemin={0}
                    aria-valuemax={j.total_units}
                    aria-valuenow={Math.min(
                      j.total_units,
                      Math.max(0, j.accepted_units),
                    )}
                  >
                    <span
                      style={{
                        width:
                          Math.min(
                            100,
                            Math.max(
                              0,
                              (j.accepted_units / j.total_units) * 100,
                            ),
                          ) + "%",
                      }}
                    />
                  </div>
                  <small>
                    {Math.round((j.accepted_units / j.total_units) * 100)}%
                  </small>
                </>
              ) : (
                <small>No units yet</small>
              )}
            </div>
            <Status value={j.state} />
          </div>
        ))}
      </section>
      {job && (
        <section className="workspace-section">
          <h2>{job.name}</h2>
          <p>{job.validation_level || "Quality not verified"}</p>
          {job.failed_units > 0 && (
            <div className="notice">
              Partial result. Failed units are not counted as accepted work.
            </div>
          )}
          <AttemptTimeline attempts={job.attempts} />
          <a href={"/jobs/" + job.id}>Open outputs in the user portal →</a>
        </section>
      )}
    </div>
  );
}
