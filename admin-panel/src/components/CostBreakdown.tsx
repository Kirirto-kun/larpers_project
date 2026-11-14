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
  const complete =
    !!data &&
    data.costs.length > 0 &&
    data.costs.every(
      (c) => c.amount !== null && Number.isFinite(c.amount) && c.amount >= 0,
    );
  const perUnit =
    complete && data && data.accepted_units > 0
      ? data.costs.reduce((sum, c) => sum + c.amount!, 0) / data.accepted_units
      : null;
  return (
    <section className="card">
      <h2>Costs</h2>
      <ErrorBox message={error} />
      {data ? (
        <>
          <p>
            {data.credits} internal credits · {data.currency}
          </p>

          <p>
            Cost per accepted unit:{" "}
            {perUnit === null
              ? "Insufficient data"
              : perUnit.toFixed(6) + " " + data.currency}
            {perUnit !== null && data.costs.some((c) => c.estimated)
              ? " (estimate)"
              : ""}
          </p>
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
        !error && <Empty>Loading…</Empty>
      )}
    </section>
  );
}
