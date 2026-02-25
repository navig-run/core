//go:build headless

// Package tray is a no-op stub used when building with the 'headless' build tag.
package tray

import (
	"go.uber.org/zap"

	"navig-core/host/internal/health"
	hostOS "navig-core/host/internal/os"
)

// Handler is a no-op tray handler for headless/server builds.
type Handler struct{}

func New(paths *hostOS.Paths, port, mode string, onQuit, onRestartPython func(), logger *zap.Logger) *Handler {
	logger.Info("tray: running headless — no system tray available")
	return &Handler{}
}

// Run is a no-op in headless mode.
func (h *Handler) Run() {}

// SetStatus is a no-op in headless mode.
func (h *Handler) SetStatus(_ health.OverallStatus) {}
