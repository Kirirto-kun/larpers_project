package telemetry

// ProcessUsage contains native cumulative process CPU time and current resident
// memory. A nil metric means unavailable; zero CPU time is a valid measurement.
type ProcessUsage struct {
	CPUTimeNS *int64
	RSSBytes  *int64
}
