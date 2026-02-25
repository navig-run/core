//go:build windows

package os

import (
	"fmt"
	"os/exec"
	"path/filepath"

	"golang.org/x/sys/windows/registry"
)

const registryRunKey = `Software\Microsoft\Windows\CurrentVersion\Run`
const registryValueName = "navig-host"

// RegisterAutostart writes the binary path to the HKCU Run registry key.
// We choose HKCU\...\Run over schtasks because:
//   - No elevation required.
//   - Automatically scoped to the current user session.
//   - Removed cleanly by deleting one registry value.
func (p *Paths) RegisterAutostart() error {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, registryRunKey, registry.SET_VALUE)
	if err != nil {
		return fmt.Errorf("autostart: open registry key: %w", err)
	}
	defer k.Close()

	exec := p.execPath
	if exec == "" {
		return fmt.Errorf("autostart: could not determine executable path")
	}
	if err := k.SetStringValue(registryValueName, `"`+exec+`"`); err != nil {
		return fmt.Errorf("autostart: write registry value: %w", err)
	}
	return nil
}

// RemoveAutostart deletes the HKCU Run registry value.
func (p *Paths) RemoveAutostart() error {
	k, err := registry.OpenKey(registry.CURRENT_USER, registryRunKey, registry.SET_VALUE)
	if err != nil {
		return fmt.Errorf("autostart: open registry key: %w", err)
	}
	defer k.Close()
	if err := k.DeleteValue(registryValueName); err != nil {
		return fmt.Errorf("autostart: delete registry value: %w", err)
	}
	return nil
}

// AutostartStatus returns true when the Run key value is present.
func (p *Paths) AutostartStatus() (enabled bool, detail string, err error) {
	k, err := registry.OpenKey(registry.CURRENT_USER, registryRunKey, registry.QUERY_VALUE)
	if err != nil {
		return false, "", fmt.Errorf("autostart: open registry key: %w", err)
	}
	defer k.Close()
	val, _, err := k.GetStringValue(registryValueName)
	if err != nil {
		return false, "", nil // value absent = disabled
	}
	return true, fmt.Sprintf("HKCU\\%s → %s", filepath.Join(registryRunKey, registryValueName), val), nil
}

// OpenLogsDir opens the log directory in Windows Explorer.
func (p *Paths) OpenLogsDir() error {
	return exec.Command("explorer", p.logDir).Start()
}

// OpenURL opens a URL in the default browser.
func (p *Paths) OpenURL(url string) error {
	return exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
}
