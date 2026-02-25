package browser

import (
	"encoding/json"
	"errors"
)

// ErrEngineNotInstalled is returned when a requested browser engine is unavailable.
var ErrEngineNotInstalled = errors.New("requested browser engine is not installed")

type BrowserDriver string

const (
	DriverChromedp   BrowserDriver = "chromedp"
	DriverPlaywright BrowserDriver = "playwright" // stub only — falls back to chromedp
	DriverStealth    BrowserDriver = "stealth"    // chromedp + NAVIG stealth patches
)

type BrowserName string

const (
	BrowserChrome   BrowserName = "chrome"
	BrowserEdge     BrowserName = "edge"
	BrowserChromium BrowserName = "chromium"
)

type ProfileName string

type AgentRequest struct {
	Id     string          `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

type AgentResponse struct {
	Id     string          `json:"id"`
	Ok     bool            `json:"ok"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *AgentError     `json:"error,omitempty"`
}

type AgentError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Interfaces & Structs

type ProfileInfo struct {
	Name    ProfileName `json:"name"`
	Path    string      `json:"path"`
	Browser BrowserName `json:"browser"`
}

type SessionLaunchConfig struct {
	ProfileName  ProfileName `json:"profileName"`
	BrowserName  BrowserName `json:"browserName"`
	Headless     bool        `json:"headless"`
	Args         []string    `json:"args"`
	DriverType   string      `json:"driverType,omitempty"`
	// CDPDebugPort, if non-zero, exposes Chrome DevTools Protocol on this port.
	// The Python Cortex sidecar reads this port to attach via CDPBridge.
	CDPDebugPort int `json:"cdpDebugPort,omitempty"`
}

type SessionInfo struct {
	SessionId string `json:"sessionId"`
}

type NewTabConfig struct {
	SessionId string `json:"sessionId"`
	Url       string `json:"url"`
}

type PageInfo struct {
	PageId string `json:"pageId"`
}

type GotoConfig struct {
	PageId string `json:"pageId"`
	Url    string `json:"url"`
}

type NavResult struct {
	Url   string `json:"url"`
	Title string `json:"title"`
}

type EvalConfig struct {
	PageId    string `json:"pageId"`
	Js        string `json:"js"`
	TimeoutMs int    `json:"timeoutMs"`
}

type EvalResult struct {
	Result json.RawMessage `json:"result"`
}

type ScreenshotConfig struct {
	PageId string `json:"pageId"`
	Path   string `json:"path"`
}

type ScreenshotResult struct {
	Path string `json:"path"`
}

type CloseSessionConfig struct {
	SessionId string `json:"sessionId"`
}

// A11ySnapshotConfig requests an ARIA snapshot from a running page.
type A11ySnapshotConfig struct {
	PageId    string `json:"pageId"`
	Selector  string `json:"selector,omitempty"` // default "body"
	TimeoutMs int    `json:"timeoutMs,omitempty"`
}

// A11ySnapshotResult holds the raw ARIA snapshot text and node count.
type A11ySnapshotResult struct {
	Snapshot  string `json:"snapshot"`
	NodeCount int    `json:"nodeCount"`
}

// ClickConfig specifies how to click an element on a page.
type ClickConfig struct {
	PageId    string `json:"pageId"`
	Selector  string `json:"selector"`
	Kind      string `json:"kind"` // "css" | "role" | "coords"
	TimeoutMs int    `json:"timeoutMs,omitempty"`
}

// FillConfig specifies how to fill a form field.
type FillConfig struct {
	PageId    string `json:"pageId"`
	Selector  string `json:"selector"`
	Kind      string `json:"kind"` // "css" | "role"
	Value     string `json:"value"`
	TimeoutMs int    `json:"timeoutMs,omitempty"`
}

// ActionResult is the generic result for click/fill operations.
type ActionResult struct {
	Ok         bool   `json:"ok"`
	Error      string `json:"error,omitempty"`
	Suggestion string `json:"suggestion,omitempty"`
}

// Driver is the interface all browser engine implementations must satisfy.
type Driver interface {
	ListBrowsers() ([]BrowserInstall, error)
	Launch(config SessionLaunchConfig) (*SessionInfo, error)
	NewTab(config NewTabConfig) (*PageInfo, error)
	Goto(config GotoConfig) (*NavResult, error)
	Eval(config EvalConfig) (*EvalResult, error)
	Screenshot(config ScreenshotConfig) (*ScreenshotResult, error)
	Close(config CloseSessionConfig) error
	// A11y methods — optional; return ErrEngineNotInstalled if unsupported.
	AriaSnapshot(config A11ySnapshotConfig) (*A11ySnapshotResult, error)
	Click(config ClickConfig) (*ActionResult, error)
	Fill(config FillConfig) (*ActionResult, error)
}

// Credentials holds authentication material. Fields are never logged
// (the emitter's redactSensitive pass strips password/token/secret keys).
type Credentials struct {
	Username string `json:"username"`
	Password string `json:"-"` // never serialized to IPC log
	TOTP     string `json:"-"` // never serialized to IPC log
}

// EngineFactory creates a Driver bound to an IPC emitter.
type EngineFactory func(emitter interface{}) Driver
