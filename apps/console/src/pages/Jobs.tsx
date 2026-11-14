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
    <>
      <PageHeader
        eyebrow="JOBS"
        title="Follow the work, including the retries."
      >
        Only accepted units count toward useful throughput.
      </PageHeader>
      <ErrorBox message={error} />
      <section className="card">
        {data?.length === 0 && <Empty>No jobs yet.</Empty>}
        {data?.map((j) => (
          <div className="row" key={j.id}>
            <div>
              <button
                className="button ghost"
                onClick={() => setSelected(j.id)}
              >
                {j.name}
              </button>
              <p>
                {j.accepted_units}/{j.total_units} accepted · {j.failed_units}{" "}
                failed
              </p>
            </div>
            <Status value={j.state} />
          </div>
        ))}
      </section>
      {job && (
        <section className="card">
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
    </>
  );
}
