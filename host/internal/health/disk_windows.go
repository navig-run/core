//go:build windows

package health

import (
	"fmt"

	"golang.org/x/sys/windows"
)

func diskFree(dir string) (int64, error) {
	var freeBytes, totalBytes, totalFreeBytes uint64
	dirPtr, err := windows.UTF16PtrFromString(dir)
	if err != nil {
		return 0, fmt.Errorf("disk free: %w", err)
	}
	if err := windows.GetDiskFreeSpaceEx(dirPtr, &freeBytes, &totalBytes, &totalFreeBytes); err != nil {
		return 0, fmt.Errorf("disk free: %w", err)
	}
	return int64(freeBytes), nil
}
