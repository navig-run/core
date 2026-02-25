// Package os provides OS-abstracted path resolution and autostart registration
// for the NAVIG host daemon.
package os

import (
	"os"
	"path/filepath"
	"runtime"
)

// Paths holds platform-resolved directories for navig-host runtime data.
type Paths struct {
	dataDir   string
	configDir string
	logDir    string
	pluginDir string
	execPath  string // absolute path to the running binary
}

// NewPaths resolves all directories based on the current OS and user.
func NewPaths() *Paths {
	p := &Paths{}
	p.execPath, _ = os.Executable()

	switch runtime.GOOS {
	case "windows":
		appData := os.Getenv("APPDATA")
		if appData == "" {
			home, _ := os.UserHomeDir()
			appData = filepath.Join(home, "AppData", "Roaming")
		}
		base := filepath.Join(appData, "navig")
		p.dataDir = base
		p.configDir = base
		p.logDir = filepath.Join(base, "logs")
		p.pluginDir = filepath.Join(base, "plugins")

	case "darwin":
		home, _ := os.UserHomeDir()
		p.dataDir = filepath.Join(home, "Library", "Application Support", "navig")
		p.configDir = filepath.Join(home, ".navig")
		p.logDir = filepath.Join(home, "Library", "Logs", "navig")
		p.pluginDir = filepath.Join(p.dataDir, "plugins")

	default: // linux and others
		home, _ := os.UserHomeDir()
		dataHome := os.Getenv("XDG_DATA_HOME")
		if dataHome == "" {
			dataHome = filepath.Join(home, ".local", "share")
		}
		configHome := os.Getenv("XDG_CONFIG_HOME")
		if configHome == "" {
			configHome = filepath.Join(home, ".config")
		}
		p.dataDir = filepath.Join(dataHome, "navig")
		p.configDir = filepath.Join(configHome, "navig")
		p.logDir = filepath.Join(p.dataDir, "logs")
		p.pluginDir = filepath.Join(p.dataDir, "plugins")
	}
	return p
}

// EnsureDirs creates all required directories (0700 permissions).
func (p *Paths) EnsureDirs() error {
	for _, d := range []string{p.dataDir, p.configDir, p.logDir, p.pluginDir} {
		if err := os.MkdirAll(d, 0700); err != nil {
			return err
		}
	}
	return nil
}

func (p *Paths) DataDir() string   { return p.dataDir }
func (p *Paths) ConfigDir() string { return p.configDir }
func (p *Paths) LogDir() string    { return p.logDir }
func (p *Paths) PluginDir() string { return p.pluginDir }
func (p *Paths) ExecPath() string  { return p.execPath }
