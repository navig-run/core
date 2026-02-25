//go:build darwin

package os

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"text/template"
)

const plistPath = "Library/LaunchAgents/com.navig.core-host.plist"

var plistTemplate = template.Must(template.New("plist").Parse(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.navig.core-host</string>
    <key>ProgramArguments</key>
    <array>
        <string>{{.Exec}}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{{.LogDir}}/navig-host.log</string>
    <key>StandardErrorPath</key>
    <string>{{.LogDir}}/navig-host-error.log</string>
</dict>
</plist>
`))

func (p *Paths) plistFilePath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, plistPath), nil
}

// RegisterAutostart writes a LaunchAgent plist to ~/Library/LaunchAgents/.
func (p *Paths) RegisterAutostart() error {
	dest, err := p.plistFilePath()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0755); err != nil {
		return fmt.Errorf("autostart: mkdir LaunchAgents: %w", err)
	}

	f, err := os.OpenFile(dest, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
	if err != nil {
		return fmt.Errorf("autostart: create plist: %w", err)
	}
	defer f.Close()

	data := struct{ Exec, LogDir string }{p.execPath, p.logDir}
	if err := plistTemplate.Execute(f, data); err != nil {
		return fmt.Errorf("autostart: write plist: %w", err)
	}
	return nil
}

// RemoveAutostart removes the LaunchAgent plist.
func (p *Paths) RemoveAutostart() error {
	dest, err := p.plistFilePath()
	if err != nil {
		return err
	}
	if err := os.Remove(dest); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("autostart: remove plist: %w", err)
	}
	return nil
}

// AutostartStatus returns true when the plist file exists.
func (p *Paths) AutostartStatus() (enabled bool, detail string, err error) {
	dest, err := p.plistFilePath()
	if err != nil {
		return false, "", err
	}
	if _, err := os.Stat(dest); err != nil {
		return false, "", nil
	}
	return true, "plist: " + dest, nil
}

// OpenLogsDir opens the log directory in Finder.
func (p *Paths) OpenLogsDir() error {
	return exec.Command("open", p.logDir).Start()
}

// OpenURL opens a URL in the default browser.
func (p *Paths) OpenURL(url string) error {
	return exec.Command("open", url).Start()
}
