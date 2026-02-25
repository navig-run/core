// navig-core/host/internal/desktop/contracts.go
package desktop

import "encoding/json"

// ─────────────────────────── Wire layer ──────────────────────────────────────

// AgentRequest is the JSON-RPC request sent to agent.py over stdin.
type AgentRequest struct {
	Id     int             `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

// AgentResponse is the JSON-RPC response received from agent.py over stdout.
// Exactly one of Result or Error will be set on a well-formed response.
type AgentResponse struct {
	Id     int             `json:"id"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *string         `json:"error,omitempty"`
}

// ─────────────────────────── Configuration ───────────────────────────────────

// DesktopAgentConfig holds all configuration needed to spawn and communicate
// with the platform-appropriate Python desktop agent sidecar.
type DesktopAgentConfig struct {
	// PythonPath is the path to the Python executable.
	// If empty, "python" (Windows) or "python3" (Linux/macOS) is used.
	PythonPath string

	// AgentScriptPath is the path to agent.py (Windows, used as fallback).
	AgentScriptPath string

	// LinuxAgentPath is the path to agent_linux.py. If empty, AgentScriptPath is used.
	LinuxAgentPath string

	// DarwinAgentPath is the path to agent_darwin.py. If empty, AgentScriptPath is used.
	DarwinAgentPath string

	// WrapperScriptPath is the path to agent_wrapper.py (cross-platform dispatcher).
	// When set, this takes precedence over all other script paths.
	WrapperScriptPath string

	// Allowlist is an optional list of window class names or titles that the
	// agent is permitted to interact with. An empty slice means no restriction.
	Allowlist []string

	// AuditLogPath is the path to the append-only JSON audit log file.
	AuditLogPath string
}

// ─────────────────────────── Structured errors ───────────────────────────────

// ErrPermissionDenied is returned by destructive methods when the client has
// not been granted explicit permission.
type ErrPermissionDenied struct{ Method string }

func (e ErrPermissionDenied) Error() string {
	return "permission denied: call requires explicit grant (method: " + e.Method + ")"
}

// ErrAuditLog is returned when the audit log cannot be opened.
type ErrAuditLog struct{ Cause error }

func (e ErrAuditLog) Error() string { return "audit log unavailable: " + e.Cause.Error() }
func (e ErrAuditLog) Unwrap() error  { return e.Cause }

// ─────────────────────────── Ping ────────────────────────────────────────────

// PingResult is the result of the "ping" method.
type PingResult struct {
	Ok bool `json:"ok"`
}

// ─────────────────────────── FindElement ─────────────────────────────────────

// FindElementParams are the parameters for the "find_element" method.
type FindElementParams struct {
	Name        *string `json:"name"`
	ClassName   *string `json:"class_name"`
	ControlType *string `json:"control_type"`
	Depth       int     `json:"depth"`
}

// ElementRect is a bounding rectangle for a UI element.
type ElementRect struct {
	Left   int `json:"left"`
	Top    int `json:"top"`
	Right  int `json:"right"`
	Bottom int `json:"bottom"`
}

// ElementInfo describes a single UI element returned by find_element.
type ElementInfo struct {
	Handle      int         `json:"handle"`
	Name        string      `json:"name"`
	ClassName   string      `json:"class_name"`
	ControlType string      `json:"control_type"`
	Rect        ElementRect `json:"rect"`
}

// ─────────────────────────── Click ───────────────────────────────────────────

// ClickParams are the parameters for the "click" method.
type ClickParams struct {
	Handle int `json:"handle"`
}

// ClickResult is the result of the "click" method.
type ClickResult struct {
	Clicked bool `json:"clicked"`
}

// ─────────────────────────── SetValue ────────────────────────────────────────

// SetValueParams are the parameters for the "set_value" method.
type SetValueParams struct {
	Handle int    `json:"handle"`
	Value  string `json:"value"`
}

// SetValueResult is the result of the "set_value" method.
type SetValueResult struct {
	// Method indicates which technique was used: "ValuePattern" or "SendKeys".
	Method string `json:"method"`
}

// ─────────────────────────── GetWindowTree ───────────────────────────────────

// GetWindowTreeParams are the parameters for the "get_window_tree" method.
type GetWindowTreeParams struct {
	Depth int `json:"depth"`
}

// WindowTreeNode represents a node in the recursive UI element tree.
type WindowTreeNode struct {
	Handle      int              `json:"handle"`
	Name        string           `json:"name"`
	ClassName   string           `json:"class_name"`
	ControlType string           `json:"control_type"`
	Rect        ElementRect      `json:"rect"`
	Children    []WindowTreeNode `json:"children,omitempty"`
}

// ─────────────────────────── AHKRun ──────────────────────────────────────────

// AHKRunParams are the parameters for the "ahk_run" method.
type AHKRunParams struct {
	Script string `json:"script"`
}

// AHKRunResult is the result of the "ahk_run" method.
type AHKRunResult struct {
	Stdout   string `json:"stdout"`
	Stderr   string `json:"stderr"`
	ExitCode int    `json:"exit_code"`
}

// ─────────────────────────── GetActionTree ───────────────────────────────────

// GetActionTreeParams are the parameters for the "get_action_tree" method.
type GetActionTreeParams struct {
	Depth  int    `json:"depth"`
	Window string `json:"window,omitempty"` // optional window title filter
}

// ActionTreeResult is the result of get_action_tree.
type ActionTreeResult struct {
	// Markdown is a numbered interactive-element tree, e.g.:
	// # Window: "GitHub"
	// [1] Button "Sign in" (rect: 100,200 - 200,230)
	// [2] TextField "Username"
	Markdown     string `json:"markdown"`
	ElementCount int    `json:"element_count"`
}

// ─────────────────────────── RunScript ───────────────────────────────────────

// RunScriptParams are the parameters for the "run_script" method.
// On Windows this runs AHK; on Linux bash; on macOS osascript.
type RunScriptParams = AHKRunParams

// ─────────────────────────── Audit log ───────────────────────────────────────

// AuditEntry is one line written to the append-only audit log.
type AuditEntry struct {
	Timestamp     string `json:"timestamp"`
	Method        string `json:"method"`
	Params        any    `json:"params"`
	ResultSummary string `json:"result_summary"`
	Error         string `json:"error,omitempty"`
}
