//go:build darwin

package discovery

import (
	"context"
	"errors"
	"regexp"
	"runtime"
	"strconv"
)

func availableCPU(ctx context.Context) (int64, error) {
	// top's second sample reflects the preceding second rather than uptime.
	raw, e := Exec(ctx, "/usr/bin/top", "-l", "2", "-s", "1", "-n", "0")
	if e != nil {
		return 0, e
	}
	matches := regexp.MustCompile(`([0-9.]+)% idle`).FindAllSubmatch(raw, -1)
	if len(matches) < 2 {
		return 0, errors.New("CPU availability sample unavailable")
	}
	idle, e := strconv.ParseFloat(string(matches[len(matches)-1][1]), 64)
	if e != nil || idle < 0 || idle > 100 {
		return 0, errors.New("invalid CPU availability")
	}
	return int64(idle * float64(runtime.NumCPU()) * 10), nil
}
