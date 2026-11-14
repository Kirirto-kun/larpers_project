import { useResource } from "../lib/hooks";
import { ErrorBox, Empty } from "./Common";
interface Credits {
  unit_version: string;
  accepted_units: number;
  credits: number;
  currency: string;
  costs: {
    name: string;
    amount: number | null;
    estimated: boolean;
    assumption: string;
  }[];
}
export function CostBreakdown() {
  const { data, error } = useResource<Credits>("/credits");
  return (
    <section className="card">
      <h2>Contribution & operating costs</h2>
      <ErrorBox message={error} />
      {data ? (
        <>
          <p>
            {data.credits} internal credits · {data.accepted_units} accepted
            units · unit version {data.unit_version}
          </p>
          <small>
            Credits are internal accounting, not paid earnings. Cost currency:{" "}
            {data.currency}.
          </small>
          {data.costs.map((c) => (
            <div className="row" key={c.name}>
              <div>
                <b>{c.name}</b>
                <p className="muted">{c.assumption}</p>
              </div>
              <span>
                {c.amount === null ? "Not measured" : c.amount.toFixed(2)}
                {c.estimated ? " (estimate)" : ""}
              </span>
            </div>
          ))}
        </>
      ) : (
        !error && <Empty>Loading cost assumptions…</Empty>
      )}
    </section>
  );
}
