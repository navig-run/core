package browser

import (
	"fmt"
	"os"
	"path/filepath"
)

type ProfileSpec struct {
	Name    ProfileName
	Dir     string
	Browser BrowserName
	Driver  BrowserDriver
}

// ResolveProfileDir returns <HOME>/.navig/browser/profiles/<name>
// cross-platform (Windows/Linux/macOS). Returns error if home dir unresolvable.
func ResolveProfileDir(profile ProfileName) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("could not resolve home directory: %v", err)
	}

	// Will naturally adapt to OS specific path separators via filepath.Join
	// e.g. ~/.navig/browser/profiles/test or C:\Users\user\.navig\browser\profiles\test
	p := filepath.Join(home, ".navig", "browser", "profiles", string(profile))
	return p, nil
}
