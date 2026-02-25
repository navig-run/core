package browserscan

import (
	"os"
)

// BrowserType identifies the core browser engine variant.
type BrowserType string

const (
	BrowserChrome BrowserType = "chrome"
	BrowserEdge   BrowserType = "edge"
	// Firefox stub for potential future expansion
	BrowserFirefox BrowserType = "firefox"
)

// Executable represents a valid discovered browser installation.
type Executable struct {
	Type BrowserType `json:"type"`
	Path string      `json:"path"`
}

// Scanner defines the platform-agnostic interface for discovering installed browsers.
type Scanner interface {
	Scan() []Executable
}

// OSScanner is the default scanner implementation for the current OS.
var OSScanner Scanner

// GetScanner returns the platform-appropriate Scanner instance.
func GetScanner() Scanner {
	if OSScanner == nil {
		OSScanner = &defaultScanner{}
	}
	return OSScanner
}

// exists is a helper to check if a file path is a valid executable file.
func exists(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	// Verify it's not a directory and is theoretically launchable.
	return !info.IsDir()
}

// expandPath is a cross-platform helper to resolve environment variable prefixes like %LOCALAPPDATA%
func expandPath(path string) string {
	return os.ExpandEnv(path)
}
