//go:build windows

package browserscan

import (
	"path/filepath"
)

type defaultScanner struct{}

func (s *defaultScanner) Scan() []Executable {
	var execs []Executable

	// 1. Chrome Paths
	chromePaths := []string{
		`$LOCALAPPDATA\Google\Chrome\Application\chrome.exe`,
		`$PROGRAMFILES\Google\Chrome\Application\chrome.exe`,
		`$PROGRAMFILES(X86)\Google\Chrome\Application\chrome.exe`,
	}

	// 2. Edge Paths
	edgePaths := []string{
		`$PROGRAMFILES(X86)\Microsoft\Edge\Application\msedge.exe`,
		`$PROGRAMFILES\Microsoft\Edge\Application\msedge.exe`,
	}

	// Discover Chrome
	for _, p := range chromePaths {
		expanded := expandWindowsEnv(p)
		if exists(expanded) {
			execs = append(execs, Executable{Type: BrowserChrome, Path: expanded})
			// Pick first valid installation for each browser type
			break
		}
	}

	// Discover Edge
	for _, p := range edgePaths {
		expanded := expandWindowsEnv(p)
		if exists(expanded) {
			execs = append(execs, Executable{Type: BrowserEdge, Path: expanded})
			break
		}
	}

	return execs
}

// expandWindowsEnv specifically handles %VAR% and $VAR style expansion seamlessly
// To map to standard os.ExpandEnv which prefers $VAR
func expandWindowsEnv(p string) string {
	cleanPath := filepath.Clean(p)
	return expandPath(cleanPath)
}
