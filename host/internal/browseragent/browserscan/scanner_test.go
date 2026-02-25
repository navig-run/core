package browserscan_test

import (
	"navig-core/host/internal/browseragent/browserscan"
	"testing"
)

func TestScanner(t *testing.T) {
	scanner := browserscan.GetScanner()
	if scanner == nil {
		t.Fatal("Expected non-nil scanner")
	}

	execs := scanner.Scan()

	// We don't guarantee a browser is installed on the CI runner,
	// but we can assert the function returns without panicking.
	t.Logf("Discovered %d browsers", len(execs))
	for _, e := range execs {
		if e.Path == "" {
			t.Errorf("Expected path, got empty string for %v", e.Type)
		}
		if e.Type != browserscan.BrowserChrome && e.Type != browserscan.BrowserEdge && e.Type != browserscan.BrowserFirefox {
			t.Errorf("Unknown browser type: %s", e.Type)
		}
		t.Logf("Found %s at %s", e.Type, e.Path)
	}
}
