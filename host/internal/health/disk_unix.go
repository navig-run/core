//go:build !windows

package health

import (
	"fmt"

	"golang.org/x/sys/unix"
)

func diskFree(dir string) (int64, error) {
	var stat unix.Statfs_t
	if err := unix.Statfs(dir, &stat); err != nil {
		return 0, fmt.Errorf("disk free: %w", err)
	}
	return int64(stat.Bavail) * int64(stat.Bsize), nil
}
