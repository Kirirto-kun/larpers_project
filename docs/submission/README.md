# Submission materials and handoff

This branch supplies the portal, pool console, HTTP benchmark collector/reporting, and written submission materials. Real-device evidence remains pending by the user's instruction on 2026-11-14. Synthetic development fixtures are not submission measurements.

| Brief requirement | Material | Current evidence |
|---|---|---|
| Source, dependencies, startup | [Usage](usage.md), [portal](../../apps/portal/README.md), [console](../../apps/console/README.md) | Pinned frontend lockfiles, builds and local checks. Full server/runner startup belongs to their branches. |
| Diagram and devices | [Inventory](inventory.md) | Architecture diagram and collection table; physical inventory pending. |
| Entry point and inputs/outputs | [Usage](usage.md) | Portal and console entry points and both user journeys. |
| Results, utilization, bottleneck | [Results](results.md), [raw-data directory](results/README.md) | Measurement procedure and report tools; physical measurements pending. |
| Product | [Product](product.md) | One-page product narrative; demand explicitly unvalidated. |
| Commercial feasibility | [Business](business.md) | One-page hypothesis and cost model; no invented prices or customers. |
| Live demo and technical value | [Demo](demo.md) | Runbook and failure contingencies; physical rehearsal pending. |

[Review and verification](review.md) records the implementation audit. [Integration handoff](integration-handoff.md) gives the final merge agent the remaining integration points. The control-plane owner can link this index from the root README.
