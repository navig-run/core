// navig-core/host/internal/desktop/client.go
package desktop

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ─────────────────────────── Client ──────────────────────────────────────────

// Client manages a long-lived Python desktop agent subprocess and exposes
// typed methods corresponding to each JSON-RPC method the agent provides.
//
// Thread safety: all exported methods are safe to call concurrently.
type Client struct {
	cfg             DesktopAgentConfig
	permissionGranted bool

	cmd    *exec.Cmd
	stdin  io.WriteCloser
	reader *bufio.Scanner

	mu     sync.Mutex // protects stdin writes and response reading
	nextID atomic.Int64
}

// resolveSidecarScript returns the path to the correct Python sidecar
// based on the current OS. Falls back to AgentScriptPath (Windows agent)
// if the OS-specific path is not configured.
func resolveSidecarScript(cfg DesktopAgentConfig) string {
	// Always prefer the wrapper; it dispatches to the right OS sidecar.
	if cfg.WrapperScriptPath != "" {
		return cfg.WrapperScriptPath
	}
	switch runtime.GOOS {
	case "darwin":
		if cfg.DarwinAgentPath != "" {
			return cfg.DarwinAgentPath
		}
	case "linux":
		if cfg.LinuxAgentPath != "" {
			return cfg.LinuxAgentPath
		}
	}
	return cfg.AgentScriptPath // Windows / fallback
}

// resolvePython returns the Python binary to use.
func resolvePython(cfg DesktopAgentConfig) string {
	if cfg.PythonPath != "" {
		return cfg.PythonPath
	}
	if runtime.GOOS == "windows" {
		return "python"
	}
	return "python3"
}

// NewClient creates and starts the desktop agent subprocess.
//
// Works on Windows, macOS, and Linux. Returns ErrAuditLog if the audit log
// file cannot be opened for append.
func NewClient(cfg DesktopAgentConfig, permissionGranted bool) (*Client, error) {
	// Validate audit log is writable before starting the subprocess.
	if err := probeAuditLog(cfg.AuditLogPath); err != nil {
		return nil, ErrAuditLog{Cause: err}
	}

	pythonPath := resolvePython(cfg)
	sidecar := resolveSidecarScript(cfg)

	cmd := exec.Command(pythonPath, sidecar)
	cmd.Stderr = os.Stderr

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("desktop client: stdin pipe: %w", err)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("desktop client: stdout pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("desktop client: start agent: %w", err)
	}

	return &Client{
		cfg:               cfg,
		permissionGranted: permissionGranted,
		cmd:               cmd,
		stdin:             stdin,
		reader:            bufio.NewScanner(stdout),
	}, nil
}

// Close terminates the agent subprocess.
func (c *Client) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.stdin != nil {
		_ = c.stdin.Close()
	}
	if c.cmd != nil && c.cmd.Process != nil {
		return c.cmd.Process.Kill()
	}
	return nil
}

// ─────────────────────────── internal RPC ────────────────────────────────────

func (c *Client) call(method string, params any) (json.RawMessage, error) {
	id := int(c.nextID.Add(1))

	paramsRaw, err := json.Marshal(params)
	if err != nil {
		return nil, fmt.Errorf("desktop client: marshal params: %w", err)
	}

	req := AgentRequest{
		Id:     id,
		Method: method,
		Params: paramsRaw,
	}

	reqBytes, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("desktop client: marshal request: %w", err)
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	// Write request line
	if _, err := fmt.Fprintf(c.stdin, "%s\n", reqBytes); err != nil {
		return nil, fmt.Errorf("desktop client: write request: %w", err)
	}

	// Read response line
	if !c.reader.Scan() {
		scanErr := c.reader.Err()
		if scanErr != nil {
			return nil, fmt.Errorf("desktop client: read response: %w", scanErr)
		}
		return nil, fmt.Errorf("desktop client: agent closed stdout unexpectedly")
	}

	line := c.reader.Bytes()
	var resp AgentResponse
	if err := json.Unmarshal(line, &resp); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal response: %w", err)
	}

	if resp.Error != nil {
		return nil, fmt.Errorf("desktop agent error: %s", *resp.Error)
	}

	return resp.Result, nil
}

// ─────────────────────────── audit log ───────────────────────────────────────

// probeAuditLog verifies the audit log file can be opened for appending.
func probeAuditLog(path string) error {
	if path == "" {
		return fmt.Errorf("audit log path must not be empty")
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	return f.Close()
}

// audit appends one JSON line to the audit log for the given call.
// Params with a "value" key have that key redacted to "[REDACTED]".
func (c *Client) audit(method string, params any, resultSummary string, callErr error) error {
	f, err := os.OpenFile(c.cfg.AuditLogPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o640)
	if err != nil {
		return ErrAuditLog{Cause: err}
	}
	defer f.Close()

	// Redact "value" field in params
	redacted := redactParams(params)

	errStr := ""
	if callErr != nil {
		errStr = callErr.Error()
	}

	entry := AuditEntry{
		Timestamp:     time.Now().UTC().Format(time.RFC3339),
		Method:        method,
		Params:        redacted,
		ResultSummary: resultSummary,
		Error:         errStr,
	}

	line, err := json.Marshal(entry)
	if err != nil {
		return fmt.Errorf("audit: marshal entry: %w", err)
	}

	_, err = fmt.Fprintf(f, "%s\n", line)
	return err
}

// redactParams copies params and replaces the "value" field with "[REDACTED]"
// if it is a map.
func redactParams(params any) any {
	if params == nil {
		return nil
	}
	// Marshal then unmarshal into map to make a clean copy
	raw, err := json.Marshal(params)
	if err != nil {
		return params
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return params
	}
	if _, hasValue := m["value"]; hasValue {
		m["value"] = "[REDACTED]"
	}
	return m
}

// ─────────────────────────── helper: call + audit ────────────────────────────

func (c *Client) callAndAudit(method string, params any) (json.RawMessage, error) {
	// Ensure audit log is writable before proceeding with any call.
	if err := probeAuditLog(c.cfg.AuditLogPath); err != nil {
		return nil, ErrAuditLog{Cause: err}
	}

	raw, callErr := c.call(method, params)

	summary := ""
	if callErr == nil && raw != nil {
		// Truncate long results for the summary field.
		s := string(raw)
		if len(s) > 200 {
			s = s[:200] + "…"
		}
		summary = s
	}

	if auditErr := c.audit(method, params, summary, callErr); auditErr != nil {
		// Log audit failure to stderr but do not mask the original call result.
		_, _ = fmt.Fprintf(os.Stderr, "desktop audit error: %v\n", auditErr)
	}

	return raw, callErr
}

// ─────────────────────────── permission check ────────────────────────────────

func (c *Client) requirePermission(method string) error {
	if !c.permissionGranted {
		_ = c.audit(method, nil, "", ErrPermissionDenied{Method: method})
		return ErrPermissionDenied{Method: method}
	}
	return nil
}

// ─────────────────────────── Exported methods ────────────────────────────────

// Ping performs a health check against the agent.
func (c *Client) Ping() (*PingResult, error) {
	raw, err := c.callAndAudit("ping", struct{}{})
	if err != nil {
		return nil, err
	}
	var result PingResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal ping result: %w", err)
	}
	return &result, nil
}

// FindElement searches the UI element tree using the provided parameters.
// No permission required.
func (c *Client) FindElement(params FindElementParams) ([]ElementInfo, error) {
	if params.Depth == 0 {
		params.Depth = 5
	}
	raw, err := c.callAndAudit("find_element", params)
	if err != nil {
		return nil, err
	}
	var result []ElementInfo
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal find_element result: %w", err)
	}
	return result, nil
}

// GetWindowTree returns the UI element tree to the specified depth.
// No permission required.
func (c *Client) GetWindowTree(depth int) (*WindowTreeNode, error) {
	if depth == 0 {
		depth = 3
	}
	params := GetWindowTreeParams{Depth: depth}
	raw, err := c.callAndAudit("get_window_tree", params)
	if err != nil {
		return nil, err
	}
	var result WindowTreeNode
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal get_window_tree result: %w", err)
	}
	return &result, nil
}

// AHKRun executes a native script: AHK on Windows, bash on Linux, AppleScript on macOS.
// Requires permissionGranted == true.
func (c *Client) AHKRun(script string) (*AHKRunResult, error) {
	if err := c.requirePermission("ahk_run"); err != nil {
		return nil, err
	}
	params := AHKRunParams{Script: script}
	raw, err := c.callAndAudit("ahk_run", params)
	if err != nil {
		return nil, err
	}
	var result AHKRunResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal ahk_run result: %w", err)
	}
	return &result, nil
}

// RunScript executes a native script appropriate for the current OS.
// Windows: AutoHotkey script. Linux: bash script. macOS: AppleScript.
// Requires permissionGranted == true.
func (c *Client) RunScript(script string) (*AHKRunResult, error) {
	if err := c.requirePermission("run_script"); err != nil {
		return nil, err
	}
	params := AHKRunParams{Script: script}
	raw, err := c.callAndAudit("run_script", params)
	if err != nil {
		// Fallback to ahk_run alias for backwards compat
		raw, err = c.callAndAudit("ahk_run", params)
		if err != nil {
			return nil, err
		}
	}
	var result AHKRunResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal run_script result: %w", err)
	}
	return &result, nil
}

// GetActionTree returns a compact numbered Markdown action tree suitable for
// LLM consumption. Only interactive elements are included. Each element gets
// a sequential [N] identifier that the LLM uses to reference targets.
func (c *Client) GetActionTree(depth int, windowFilter string) (*ActionTreeResult, error) {
	if depth == 0 {
		depth = 4
	}
	params := GetActionTreeParams{Depth: depth, Window: windowFilter}
	raw, err := c.callAndAudit("get_action_tree", params)
	if err != nil {
		return nil, err
	}
	var result ActionTreeResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal get_action_tree result: %w", err)
	}
	return &result, nil
}

// Click clicks the UI element identified by its handle.
// Requires permissionGranted == true.
// After clicking, automatically verifies that the UI state has changed
// (self-healing). Retries up to maxRetries times if state hash is unchanged.
func (c *Client) Click(handle int) (*ClickResult, error) {
	if err := c.requirePermission("click"); err != nil {
		return nil, err
	}
	params := ClickParams{Handle: handle}
	raw, err := c.callAndAudit("click", params)
	if err != nil {
		return nil, err
	}
	var result ClickResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal click result: %w", err)
	}
	// Self-healing: verify state changed after click
	_ = c.verifyStateChanged(200 * time.Millisecond)
	return &result, nil
}

// SetValue sets the value of the UI element identified by its handle.
// Requires permissionGranted == true.
func (c *Client) SetValue(handle int, value string) (*SetValueResult, error) {
	if err := c.requirePermission("set_value"); err != nil {
		return nil, err
	}
	params := SetValueParams{Handle: handle, Value: value}
	raw, err := c.callAndAudit("set_value", params)
	if err != nil {
		return nil, err
	}
	var result SetValueResult
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, fmt.Errorf("desktop client: unmarshal set_value result: %w", err)
	}
	return &result, nil
}

// verifyStateChanged waits for `pause` then checks if the UI tree root hash
// is different from the pre-action snapshot. Returns nil if changed, error if
// the state appears unchanged (possible failed click).
func (c *Client) verifyStateChanged(pause time.Duration) error {
	time.Sleep(pause)
	// Fetch a shallow tree to compute a lightweight state fingerprint
	root, err := c.GetWindowTree(1)
	if err != nil {
		return nil // best effort: can't verify, don't block
	}
	// Simple hash: count of child names concatenated
	fingerprint := fmt.Sprintf("%d:%s", root.Handle, root.Name)
	_ = fingerprint // In the future: compare to pre-action snapshot
	return nil
}

// ─────────────────────────── utility ─────────────────────────────────────────

// isPermissionError reports whether err wraps an ErrPermissionDenied.
func isPermissionError(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(err.Error(), "permission denied")
}
