package discovery

import (
	"regexp"
	"strconv"
	"strings"
)

func parseVMStat(text string) int64 {
	match := regexp.MustCompile(`page size of ([0-9]+) bytes`).FindStringSubmatch(text)
	if len(match) != 2 {
		return 0
	}
	page, e := strconv.ParseInt(match[1], 10, 64)
	if e != nil || page <= 0 || page > 1<<20 {
		return 0
	}
	var pages int64
	for _, line := range strings.Split(text, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		name := strings.TrimSpace(parts[0])
		if name != "Pages free" && name != "Pages inactive" && name != "Pages speculative" {
			continue
		}
		n, e := strconv.ParseInt(strings.TrimSuffix(strings.TrimSpace(parts[1]), "."), 10, 64)
		if e != nil || n < 0 || n > 1<<40 {
			return 0
		}
		pages += n
	}
	return pages * page
}
