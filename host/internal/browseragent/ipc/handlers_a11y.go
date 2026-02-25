// Package ipc — A11y IPC handler registration.
//
// Registers three new JSON-RPC methods that expose the cortex a11y pipeline
// to any IPC caller (Go host, Python sidecar selector, CLI):
//
//   Browser.AriaSnapshot  — returns a Playwright-format ARIA tree text
//   Browser.Click         — structured click with kind: css|role|coords
//   Browser.Fill          — JS value-injection fill with structured result
package ipc

import (
	"encoding/json"

	"navig-core/host/internal/browser"
)

// A11yDriverContext is satisfied by any browser.Driver implementation
// that supports the a11y methods. The router checks ErrEngineNotInstalled
// and returns a clean JSON error if unsupported.
type A11yDriverContext interface {
	AriaSnapshot(config browser.A11ySnapshotConfig) (*browser.A11ySnapshotResult, error)
	Click(config browser.ClickConfig) (*browser.ActionResult, error)
	Fill(config browser.FillConfig) (*browser.ActionResult, error)
}

// RegisterA11yHandlers binds the three a11y methods to the IPC server.
// Call this after RegisterBrowserHandlers in main/navbrowser.go.
func RegisterA11yHandlers(server *Server, driver A11yDriverContext) {
	// Browser.AriaSnapshot
	server.Handlers["Browser.AriaSnapshot"] = func(params json.RawMessage) (interface{}, error) {
		var req browser.A11ySnapshotConfig
		if err := json.Unmarshal(params, &req); err != nil {
			return nil, err
		}
		return driver.AriaSnapshot(req)
	}

	// Browser.Click
	server.Handlers["Browser.Click"] = func(params json.RawMessage) (interface{}, error) {
		var req browser.ClickConfig
		if err := json.Unmarshal(params, &req); err != nil {
			return nil, err
		}
		return driver.Click(req)
	}

	// Browser.Fill
	server.Handlers["Browser.Fill"] = func(params json.RawMessage) (interface{}, error) {
		var req browser.FillConfig
		if err := json.Unmarshal(params, &req); err != nil {
			return nil, err
		}
		return driver.Fill(req)
	}
}
