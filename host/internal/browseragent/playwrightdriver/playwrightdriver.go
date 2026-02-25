// Package playwrightdriver is a stub for the Playwright engine.
//
// The canonical Playwright integration lives in the Python layer
// (navig/browser/stealth.py via patchright). The Go browser sidecar
// uses chromedp as its only engine. This stub ensures that if a caller
// requests the "playwright" driver type, the router falls back to chromedp
// gracefully via ErrEngineNotInstalled — no panic, no crash.
package playwrightdriver

import (
	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/ipc"
)

// Driver is the Playwright stub. All methods return ErrEngineNotInstalled.
type Driver struct{}

// New returns a stub Driver. The router checks for ErrEngineNotInstalled
// and selects chromedp automatically.
func New(_ ipc.Emitter) *Driver { return &Driver{} }

func (d *Driver) ListBrowsers() ([]browser.BrowserInstall, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Launch(_ browser.SessionLaunchConfig) (*browser.SessionInfo, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) NewTab(_ browser.NewTabConfig) (*browser.PageInfo, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Goto(_ browser.GotoConfig) (*browser.NavResult, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Eval(_ browser.EvalConfig) (*browser.EvalResult, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Screenshot(_ browser.ScreenshotConfig) (*browser.ScreenshotResult, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Close(_ browser.CloseSessionConfig) error {
	return browser.ErrEngineNotInstalled
}

// A11y stubs — playwrightdriver always defers to the Python sidecar.

func (d *Driver) AriaSnapshot(_ browser.A11ySnapshotConfig) (*browser.A11ySnapshotResult, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Click(_ browser.ClickConfig) (*browser.ActionResult, error) {
	return nil, browser.ErrEngineNotInstalled
}

func (d *Driver) Fill(_ browser.FillConfig) (*browser.ActionResult, error) {
	return nil, browser.ErrEngineNotInstalled
}
