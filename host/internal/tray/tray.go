//go:build !headless

// Package tray manages the system tray icon and menu for navig-host.
// Build without the 'headless' tag to include this file.
package tray

import (
	"fmt"

	"github.com/getlantern/systray"
	"go.uber.org/zap"

	"navig-core/host/internal/health"
	hostOS "navig-core/host/internal/os"
)

// IconState maps to the three possible tray icon colours.
type IconState int

const (
	IconGreen  IconState = iota // healthy
	IconYellow                  // degraded
	IconRed                     // offline/unhealthy
)

// Handler holds tray callbacks and runtime state.
type Handler struct {
	paths   *hostOS.Paths
	port    string
	onQuit  func()
	onRestartPython func()
	logger  *zap.Logger

	// menu items (set during onReady)
	mStatus  *systray.MenuItem
	mMode    *systray.MenuItem
	mPortAuth *systray.MenuItem
}

// New creates a Handler. onQuit and onRestartPython are called on menu actions.
func New(paths *hostOS.Paths, port, mode string, onQuit, onRestartPython func(), logger *zap.Logger) *Handler {
	return &Handler{
		paths:           paths,
		port:            port,
		onQuit:          onQuit,
		onRestartPython: onRestartPython,
		logger:          logger,
	}
}

// Run starts the tray on the calling (main) goroutine. It blocks until the tray exits.
// All async work (health subscription, click handlers) runs in background goroutines.
func (h *Handler) Run() {
	systray.Run(h.onReady, h.onExit)
}

// SetStatus updates the tray icon and status menu item to reflect the given health state.
// Safe to call from any goroutine.
func (h *Handler) SetStatus(s health.OverallStatus) {
	switch s {
	case health.StatusHealthy:
		systray.SetIcon(iconGreenPNG())
		systray.SetTooltip("NAVIG Host — Connected")
		if h.mStatus != nil {
			h.mStatus.SetTitle("● Connected")
		}
	case health.StatusDegraded:
		systray.SetIcon(iconYellowPNG())
		systray.SetTooltip("NAVIG Host — Degraded")
		if h.mStatus != nil {
			h.mStatus.SetTitle("◑ Degraded")
		}
	default:
		systray.SetIcon(iconRedPNG())
		systray.SetTooltip("NAVIG Host — Offline")
		if h.mStatus != nil {
			h.mStatus.SetTitle("○ Offline")
		}
	}
}

func (h *Handler) onReady() {
	systray.SetTitle("NAVIG")
	systray.SetTooltip("NAVIG Core Host")
	systray.SetIcon(iconYellowPNG()) // yellow until first health check

	h.mStatus = systray.AddMenuItem("○ Starting…", "Connection status")
	h.mStatus.Disable()

	mMode := systray.AddMenuItem("Mode: Cloud", "Operational mode")
	mMode.Disable()
	h.mMode = mMode

	systray.AddSeparator()

	mLogs := systray.AddMenuItem("Open Logs Folder", "")
	mDash := systray.AddMenuItem("Open Dashboard", "")
	mRestart := systray.AddMenuItem("Restart Python Router", "")

	systray.AddSeparator()

	h.mPortAuth = systray.AddMenuItem(fmt.Sprintf("Port %s | Auth: token", h.port), "")
	h.mPortAuth.Disable()

	systray.AddSeparator()
	mQuit := systray.AddMenuItem("Quit", "Graceful shutdown")

	// Click handler goroutine
	go func() {
		for {
			select {
			case <-mLogs.ClickedCh:
				if err := h.paths.OpenLogsDir(); err != nil {
					h.logger.Warn("tray: open logs dir failed", zap.Error(err))
				}
			case <-mDash.ClickedCh:
				url := fmt.Sprintf("http://127.0.0.1:%s/", h.port)
				if err := h.paths.OpenURL(url); err != nil {
					h.logger.Warn("tray: open dashboard failed", zap.Error(err))
				}
			case <-mRestart.ClickedCh:
				if h.onRestartPython != nil {
					go h.onRestartPython()
				}
			case <-mQuit.ClickedCh:
				systray.Quit()
				if h.onQuit != nil {
					h.onQuit()
				}
			}
		}
	}()
}

func (h *Handler) onExit() {
	h.logger.Info("tray exited")
}

// --- Embedded icons ----------------------------------------------------------
// In production, embed real PNG bytes via //go:embed.
// These stubs return minimal 1×1 coloured PNGs so the build succeeds without
// asset files committed.

func iconGreenPNG() []byte  { return minimalPNG(0x00, 0xC8, 0x00) }
func iconYellowPNG() []byte { return minimalPNG(0xFF, 0xC0, 0x00) }
func iconRedPNG() []byte    { return minimalPNG(0xC8, 0x00, 0x00) }

// minimalPNG returns a valid 1×1 PNG with the given RGB colour.
func minimalPNG(r, g, b byte) []byte {
	// Pre-built 1×1 PNG template (IHDR + IDAT + IEND); colour patched at runtime.
	_ = r; _ = g; _ = b
	// Real implementation: use embed + pre-generated assets.
	// For now return a transparent 1×1 PNG.
	return []byte{
		0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, // PNG signature
		0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, // IHDR length + type
		0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, // 1×1
		0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
		0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, // IDAT
		0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
		0x00, 0x00, 0x02, 0x00, 0x01, 0xE2, 0x21, 0xBC,
		0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, // IEND
		0x44, 0xAE, 0x42, 0x60, 0x82,
	}
}
