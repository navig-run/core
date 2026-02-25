package ipc

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

type Event struct {
	Type      string      `json:"type"`  // "event"
	Name      string      `json:"name"`  // "status" | "heartbeat" | "artifact" | "log" | "error"
	TS        string      `json:"ts"`    // ISO 8601 with milliseconds
	Level     string      `json:"level"` // "debug" | "info" | "warn" | "error"
	SessionID string      `json:"sessionId,omitempty"`
	PageID    string      `json:"pageId,omitempty"`
	Data      interface{} `json:"data"`
}

type EventCtx struct {
	SessionID string
	PageID    string
}

// Data shapes

type StatusData struct {
	Phase   string      `json:"phase"`
	Step    string      `json:"step,omitempty"`
	Message string      `json:"message"`
	Pct     *int        `json:"pct,omitempty"`
	Details interface{} `json:"details,omitempty"`
}

type HeartbeatData struct {
	State   string `json:"state"`
	SinceMs int64  `json:"sinceMs"`
	Hint    string `json:"hint,omitempty"`
}

type ArtifactData struct {
	Kind string `json:"kind"`
	Path string `json:"path"`
}

type LifecycleData struct {
	Event    string `json:"event"`              // "Launch", "Navigate", "Extract", "Close", "DriverFallback", "SessionAborted"
	URL      string `json:"url,omitempty"`      // The target URL (if applicable)
	Fallback string `json:"fallback,omitempty"` // The fallback state (if applicable)
}

type ErrorData struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

type Emitter interface {
	WriteJSONLine(msg interface{})
	Status(ctx EventCtx, level string, data StatusData)
	Heartbeat(ctx EventCtx, data HeartbeatData)
	Artifact(ctx EventCtx, data ArtifactData)
	Lifecycle(ctx EventCtx, data LifecycleData)
	Error(ctx EventCtx, data ErrorData)
}

type stdoutEmitter struct {
	mu  *sync.Mutex
	out io.Writer
}

func NewStdoutEmitter(mu *sync.Mutex) Emitter {
	if mu == nil {
		mu = &sync.Mutex{}
	}
	return &stdoutEmitter{
		mu:  mu,
		out: os.Stdout,
	}
}

func (e *stdoutEmitter) WriteJSONLine(msg interface{}) {
	b, err := json.Marshal(msg)
	if err != nil {
		// Minimum fallback error event
		fallback := fmt.Sprintf(`{"type":"event","name":"error","ts":"%s","level":"error","data":{"code":"NAV_INTERNAL","message":"failed to marshal event"}}`,
			time.Now().UTC().Format(time.RFC3339Nano[:23]+"Z"))
		e.mu.Lock()
		fmt.Fprintln(os.Stderr, fallback)
		e.mu.Unlock()
		return
	}

	e.mu.Lock()
	defer e.mu.Unlock()
	fmt.Fprintln(e.out, string(b))
}

func (e *stdoutEmitter) emitEvent(ctx EventCtx, name string, level string, data interface{}) {
	ts := time.Now().UTC().Format(time.RFC3339Nano)
	if len(ts) > 23 {
		// Truncate to milliseconds: "2006-01-02T15:04:05.123Z"
		ts = ts[:23] + "Z"
	}
	ev := Event{
		Type:      "event",
		Name:      name,
		TS:        ts,
		Level:     level,
		SessionID: ctx.SessionID,
		PageID:    ctx.PageID,
		Data:      data,
	}
	e.WriteJSONLine(ev)
}

func (e *stdoutEmitter) Status(ctx EventCtx, level string, data StatusData) {
	e.emitEvent(ctx, "status", level, data)
}

func (e *stdoutEmitter) Heartbeat(ctx EventCtx, data HeartbeatData) {
	e.emitEvent(ctx, "heartbeat", "debug", data)
}

func (e *stdoutEmitter) Artifact(ctx EventCtx, data ArtifactData) {
	e.emitEvent(ctx, "artifact", "info", data)
}

func (e *stdoutEmitter) Lifecycle(ctx EventCtx, data LifecycleData) {
	e.emitEvent(ctx, "lifecycle", "info", data)
}

func (e *stdoutEmitter) Error(ctx EventCtx, data ErrorData) {
	e.emitEvent(ctx, "error", "error", data)
}
