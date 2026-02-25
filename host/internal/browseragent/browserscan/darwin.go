//go:build darwin

package browserscan

type defaultScanner struct{}

func (s *defaultScanner) Scan() []Executable {
	var execs []Executable

	// macOS standard Application paths
	chromePath := "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
	edgePath := "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"

	if exists(chromePath) {
		execs = append(execs, Executable{Type: BrowserChrome, Path: chromePath})
	}

	if exists(edgePath) {
		execs = append(execs, Executable{Type: BrowserEdge, Path: edgePath})
	}

	return execs
}
