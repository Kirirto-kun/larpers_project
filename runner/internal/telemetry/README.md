# Local samples and foundation telemetry

`Sample` is the local diagnostic/baseline format. It retains UTC nanosecond
window boundaries, duration, source kind, and the original measured values.
Standalone runs continue to store these raw samples. `Sample.Wire` creates a
separate generated foundation `Telemetry` DTO without changing the local data.

Foundation only accepts sequence, phase, optional CPU/GPU percentages, and
optional memory bytes. It has no window or duration, sensor availability, device
identity, or metric-scope fields. These wire DTOs cannot preserve the precise
local measurement windows or establish formal performance evidence. This change
does not establish server retention of raw samples or formal benchmark approval.

CPU percentages on the wire use these declared denominators:

- Node: runner process CPU time divided by elapsed time and all logical host
  cores. This measures the runner process's share of host capacity, rather than
  whole-host CPU usage. The local value remains percent of one logical core.
- Attempt: Docker's one-core CPU percentage divided by allocated
  `cpu_millis / 1000`, including fractional allocations. Wire values saturate at
  100 to fit foundation's bound; raw overshoots remain in the local sample.

GPU percentage is the arithmetic mean of the selected devices' measured
utilization, only when every selected device has a valid reading. Device-wide
utilization includes other processes and is not per-attempt GPU accounting.
Missing devices, unavailable sensors, no GPU, and malformed readings remain
unknown (omitted), while a measured zero remains zero. GPU memory is retained
locally and is not combined with the wire's process/container memory field.
Docker's human-readable memory usage is converted to bytes for wire transport;
its original text remains in local samples.

CPU and GPU attempt sends share a mutex-protected sequence and HTTP send order,
with generation and lease token at the top level of `AttemptTelemetry`.

The idle acceptance test observes the actual CLI child PID from its parent,
using native Windows process times/current working set or Linux `/proc` counters
with the host's queried `CLK_TCK`. Its raw native windows are independent of API
receipt times. macOS external PID CPU/current RSS measurement is explicitly
pending and the idle acceptance test skips there; a peak RSS counter is not
substituted for current RSS. The test's API DTO capture is recorded separately
from the raw native samples.
