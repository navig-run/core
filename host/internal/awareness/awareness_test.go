package awareness

import (
	"encoding/json"
	"testing"
)

func TestExtractNodeIntelligence(t *testing.T) {
	intel := ExtractNodeIntelligence()

	if intel.Hostname == "" {
		t.Error("Expected a hostname, got empty string")
	}
	if intel.OS == "" {
		t.Error("Expected an OS, got empty string")
	}
	if intel.CPUs <= 0 {
		t.Errorf("Expected CPUs > 0, got %d", intel.CPUs)
	}

	// Print JSON for verification in test logs
	b, _ := json.MarshalIndent(intel, "", "  ")
	t.Logf("Extracted Node Intelligence:\n%s", string(b))
}
