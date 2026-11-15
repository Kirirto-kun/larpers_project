type Sample = {
  sequence: number;
  cpu_percent: number | null;
  gpu_percent: number | null;
};
export function UtilizationChart({ samples }: { samples: Sample[] }) {
  const series = [
    { key: "cpu_percent" as const, label: "CPU", color: "#24533b" },
    { key: "gpu_percent" as const, label: "GPU", color: "#356785" },
  ];
  const min = Math.min(...samples.map((s) => s.sequence));
  const max = Math.max(min + 1, ...samples.map((s) => s.sequence));
  const x = (sequence: number) => 40 + (520 * (sequence - min)) / (max - min);
  const y = (value: number) => 160 - value * 1.3;
  return (
    <figure className="telemetry-chart">
      <svg
        viewBox="0 0 600 200"
        role="img"
        aria-label="CPU and GPU utilization by device sample"
      >
        {[0, 50, 100].map((v) => (
          <g key={v}>
            <line x1="40" x2="570" y1={y(v)} y2={y(v)} stroke="#d5dfd1" />
            <text x="2" y={y(v) + 4} fill="#526454" fontSize="13">
              {v}%
            </text>
          </g>
        ))}
        {series.map(({ key, color }) => {
          const segments: Sample[][] = [];
          let part: Sample[] = [];
          for (const sample of samples) {
            if (
              sample[key] == null ||
              (part.length && sample.sequence !== part.at(-1)!.sequence + 1)
            ) {
              if (part.length) segments.push(part);
              part = [];
            }
            if (sample[key] != null) part.push(sample);
          }
          if (part.length) segments.push(part);
          return (
            <g key={key}>
              {segments.map((segment, i) => (
                <g key={i}>
                  <polyline
                    fill="none"
                    stroke={color}
                    strokeWidth="3"
                    points={segment
                      .map((s) => `${x(s.sequence)},${y(s[key]!)}`)
                      .join(" ")}
                  />
                  {segment.map((s) => (
                    <circle
                      key={s.sequence}
                      cx={x(s.sequence)}
                      cy={y(s[key]!)}
                      r="4"
                      fill={color}
                    />
                  ))}
                </g>
              ))}
            </g>
          );
        })}
        <text x="40" y="190" fill="#526454" fontSize="13">
          Device samples
        </text>
      </svg>
      <figcaption>
        CPU: green · GPU: blue. Gaps indicate missing measurements.
      </figcaption>
    </figure>
  );
}
