//go:build linux

package browserscan

type defaultScanner struct{}

func (s *defaultScanner) Scan() []Executable {
	var execs []Executable

	// 1. Chrome / Chromium variations
	chromeCmds := []string{
		"google-chrome",
		"google-chrome-stable",
		"chromium",
		"chromium-browser",
		"/usr/bin/google-chrome",
		"/usr/bin/chromium",
	}

	// 2. Edge variations
	edgeCmds := []string{
		"microsoft-edge",
		"microsoft-edge-stable",
		"/usr/bin/microsoft-edge-stable",
	}

	for _, cmd := range chromeCmds {
		if exists(cmd) {
			execs = append(execs, Executable{Type: BrowserChrome, Path: cmd})
			break
		}
	}

	for _, cmd := range edgeCmds {
		if exists(cmd) {
			execs = append(execs, Executable{Type: BrowserEdge, Path: cmd})
			break
		}
	}

	return execs
}
