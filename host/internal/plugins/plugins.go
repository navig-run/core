package plugins

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"go.uber.org/zap"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"

	"navig-core/host/internal/config"
	"navig-core/host/internal/events"
	hostOS "navig-core/host/internal/os"
)

// Plugin represents a managed Python subprocess.
type Plugin struct {
	Name    string
	Script  string
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	mu      sync.Mutex
	nextID  int
	pending map[int]chan *rpcResponse
}

type rpcRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      int         `json:"id"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params,omitempty"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      int             `json:"id"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

// Manager supervises all plugin subprocesses.
type Manager struct {
	cfg     config.PluginsConfig
	bus     *events.Bus
	paths   *hostOS.Paths
	logger  *zap.SugaredLogger
	plugins map[string]*Plugin
	mu      sync.Mutex
	ctx     context.Context
	cancel  context.CancelFunc
}

// NewManager creates a plugin manager (does not start plugins yet).
func NewManager(cfg config.PluginsConfig, bus *events.Bus, paths *hostOS.Paths, logger *zap.SugaredLogger) *Manager {
	ctx, cancel := context.WithCancel(context.Background())
	return &Manager{
		cfg:     cfg,
		bus:     bus,
		paths:   paths,
		logger:  logger,
		plugins: make(map[string]*Plugin),
		ctx:     ctx,
		cancel:  cancel,
	}
}

// Start discovers and launches all *.py files in the plugins directory.
func (m *Manager) Start() error {
	dir := m.cfg.Dir
	if dir == "" {
		dir = filepath.Join(m.paths.DataDir(), "plugins")
	}
	if _, err := os.Stat(dir); os.IsNotExist(err) {
		m.logger.Info("plugins dir not found, skipping", "dir", dir)
		return nil
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return fmt.Errorf("plugins: read dir: %w", err)
	}

	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".py" {
			continue
		}
		script := filepath.Join(dir, e.Name())
		name := e.Name()[:len(e.Name())-3]
		if err := m.launch(name, script); err != nil {
			m.logger.Error("failed to launch plugin", "name", name, "err", err)
			m.bus.PublishAsync(events.TopicPluginError, map[string]string{"plugin": name, "error": err.Error()})
		}
	}
	return nil
}

// Stop shuts down all plugins.
func (m *Manager) Stop() {
	m.cancel()
	m.mu.Lock()
	defer m.mu.Unlock()
	for name, p := range m.plugins {
		p.mu.Lock()
		if p.cmd != nil && p.cmd.Process != nil {
			_ = p.cmd.Process.Kill()
		}
		p.mu.Unlock()
		m.logger.Info("plugin stopped", "name", name)
		m.bus.Publish(events.TopicPluginStopped, map[string]string{"plugin": name})
	}
}

// Call sends a JSON-RPC request to the named plugin and waits for a response.
func (m *Manager) Call(name, method string, params interface{}, timeout time.Duration) (json.RawMessage, error) {
	m.mu.Lock()
	p, ok := m.plugins[name]
	m.mu.Unlock()
	if !ok {
		return nil, fmt.Errorf("plugins: unknown plugin %q", name)
	}
	return p.call(method, params, timeout)
}

// --- internal ---

func (m *Manager) launch(name, script string) error {
	python := m.cfg.PythonPath
	if python == "" {
		python = "python3"
	}

	cmd := exec.CommandContext(m.ctx, python, "-u", script)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr

	p := &Plugin{
		Name:    name,
		Script:  script,
		cmd:     cmd,
		stdin:   stdin,
		pending: make(map[int]chan *rpcResponse),
	}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("plugins: start %s: %w", name, err)
	}

	m.mu.Lock()
	m.plugins[name] = p
	m.mu.Unlock()

	go p.readLoop(stdout, m.logger)
	m.logger.Info("plugin started", "name", name, "script", script)
	m.bus.PublishAsync(events.TopicPluginStarted, map[string]string{"plugin": name})
	return nil
}

func (p *Plugin) call(method string, params interface{}, timeout time.Duration) (json.RawMessage, error) {
	p.mu.Lock()
	p.nextID++
	id := p.nextID
	ch := make(chan *rpcResponse, 1)
	p.pending[id] = ch
	p.mu.Unlock()

	req := rpcRequest{JSONRPC: "2.0", ID: id, Method: method, Params: params}
	b, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	b = append(b, '\n')

	p.mu.Lock()
	_, err = p.stdin.Write(b)
	p.mu.Unlock()
	if err != nil {
		return nil, err
	}

	select {
	case resp := <-ch:
		if resp.Error != nil {
			return nil, fmt.Errorf("plugins rpc error %d: %s", resp.Error.Code, resp.Error.Message)
		}
		return resp.Result, nil
	case <-time.After(timeout):
		p.mu.Lock()
		delete(p.pending, id)
		p.mu.Unlock()
		return nil, fmt.Errorf("plugins: rpc timeout calling %s", method)
	}
}

func (p *Plugin) readLoop(r io.Reader, logger *zap.SugaredLogger) {
	scanner := bufio.NewScanner(r)
	for scanner.Scan() {
		line := scanner.Bytes()
		var resp rpcResponse
		if err := json.Unmarshal(line, &resp); err != nil {
			logger.Warn("plugin: bad response", "plugin", p.Name, "line", string(line))
			continue
		}
		p.mu.Lock()
		ch, ok := p.pending[resp.ID]
		if ok {
			delete(p.pending, resp.ID)
		}
		p.mu.Unlock()
		if ok {
			ch <- &resp
		}
	}
}
