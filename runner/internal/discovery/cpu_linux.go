//go:build linux

package discovery

import (
	"context"
	"errors"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func availableCPU(ctx context.Context) (int64, error) {
	return sampleIdleCPU(ctx, runtime.NumCPU(), 200*time.Millisecond, func() (uint64, uint64, error) {
		raw, e := os.ReadFile("/proc/stat")
		if e != nil {
			return 0, 0, e
		}
		fields := strings.Fields(strings.SplitN(string(raw), "\n", 2)[0])
		if len(fields) < 5 || fields[0] != "cpu" {
			return 0, 0, errors.New("CPU counters unavailable")
		}
		var total, idle uint64
		// Guest values are already included in user/nice.
		for i, field := range fields[1:min(len(fields), 9)] {
			n, e := strconv.ParseUint(field, 10, 64)
			if e != nil {
				return 0, 0, e
			}
			total += n
			if i == 3 || i == 4 {
				idle += n
			}
		}
		return idle, total, nil
	})
}
