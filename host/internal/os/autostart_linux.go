//go:build linux

package os

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"text/template"
)

const (
	serviceFilename = "navig-core-host.service"
	systemdUserDir  = ".config/systemd/user"
)

var serviceTemplate = template.Must(template.New("service").Parse(`[Unit]
Description=NAVIG Core Host daemon
After=network.target

[Service]
Type=simple
ExecStart={{.Exec}}
Restart=on-failure
RestartSec=5
StandardOutput=append:{{.LogDir}}/navig-host.log
StandardError=append:{{.LogDir}}/navig-host-error.log

[Install]
WantedBy=default.target
`))

func (p *Paths) serviceFilePath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, systemdUserDir, serviceFilename), nil
}

// RegisterAutostart writes a systemd user service unit and enables it.
func (p *Paths) RegisterAutostart() error {
	dest, err := p.serviceFilePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0755); err != nil {
		return fmt.Errorf("autostart: mkdir systemd user dir: %w", err)
	}

	f, err := os.OpenFile(dest, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("autostart: create service file: %w", err)
	}
	defer f.Close()

	data := struct{ Exec, LogDir string }{p.execPath, p.logDir}
	if err := serviceTemplate.Execute(f, data); err != nil {
		return fmt.Errorf("autostart: write service file: %w", err)
	}
	f.Close()

	// systemctl --user daemon-reload; enable
	if out, err := exec.Command("systemctl", "--user", "daemon-reload").CombinedOutput(); err != nil {
		return fmt.Errorf("autostart: daemon-reload: %w — %s", err, string(out))
	}
	if out, err := exec.Command("systemctl", "--user", "enable", serviceFilename).CombinedOutput(); err != nil {
		return fmt.Errorf("autostart: enable service: %w — %s", err, string(out))
	}
	return nil
}

// RemoveAutostart disables and removes the systemd user service.
func (p *Paths) RemoveAutostart() error {
	dest, err := p.serviceFilePath()
	if err != nil {
		return err
	}

	// Disable (ignore errors if not enabled)
	_ = exec.Command("systemctl", "--user", "disable", "--now", serviceFilename).Run()

	if err := os.Remove(dest); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("autostart: remove service file: %w", err)
	}
	_ = exec.Command("systemctl", "--user", "daemon-reload").Run()
	return nil
}

// AutostartStatus returns whether the service unit file exists and is enabled.
func (p *Paths) AutostartStatus() (enabled bool, detail string, err error) {
	dest, err := p.serviceFilePath()
	if err != nil {
		return false, "", err
	}
	if _, statErr := os.Stat(dest); statErr != nil {
		return false, "", nil
	}
	out, _ := exec.Command("systemctl", "--user", "is-enabled", serviceFilename).Output()
	state := string(out)
	return true, fmt.Sprintf("unit: %s (%s)", dest, state), nil
}

// OpenLogsDir opens the log directory using xdg-open.
func (p *Paths) OpenLogsDir() error {
	return exec.Command("xdg-open", p.logDir).Start()
}

// OpenURL opens a URL using xdg-open.
func (p *Paths) OpenURL(url string) error {
	return exec.Command("xdg-open", url).Start()
}
