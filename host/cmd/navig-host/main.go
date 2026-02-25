package main

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/spf13/cobra"
	"go.uber.org/zap"

	"navig-core/host/internal/apiv1"
	"navig-core/host/internal/config"
	"navig-core/host/internal/events"
	"navig-core/host/internal/gateway"
	"navig-core/host/internal/health"
	"navig-core/host/internal/logging"
	"navig-core/host/internal/metrics"
	hostOS "navig-core/host/internal/os"
	"navig-core/host/internal/token"
	"navig-core/host/internal/tray"
)

var rootCmd = &cobra.Command{
	Use:   "navig-host",
	Short: "NAVIG host daemon",
	RunE:  run,
}

var cfgFile string

func init() {
	rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file path (default: ~/.navig/config.yaml)")
}

func main() {
	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

func run(cmd *cobra.Command, args []string) error {
	// --- OS paths ---
	paths := hostOS.NewPaths()
	if err := paths.EnsureDirs(); err != nil {
		return err
	}

	// --- Logging ---
	logger, err := logging.New(paths.LogDir(), "info")
	if err != nil {
		return fmt.Errorf("logger init: %w", err)
	}
	defer logger.Sync()
	defer logger.RecoverAndWrite(paths.LogDir())

	zl := logger.Logger // *zap.Logger

	// --- Config ---
	cfg, err := config.Load(cfgFile)
	if err != nil {
		return err
	}
	zl.Info("navig-host starting", zap.String("addr", cfg.API.Addr))

	// --- Event bus ---
	bus := events.NewBus()
	_ = bus

	// --- Token store ---
	slogLogger := zap.NewStdLog(zl).Writer()
	_ = slogLogger
	tokenStore := token.NewStore(cfg.Keyring.Service, nil)

	// --- Gateway (Python subprocess) ---
	gw := gateway.NewManager(gateway.Config{
		PythonPath: cfg.Plugins.PythonPath,
	}, zl.Sugar())
	if err := gw.Start(); err != nil {
		zl.Warn("gateway start error", zap.Error(err))
	}
	defer gw.Stop()

	// --- Metrics ---
	met := metrics.New()

	// Hook gateway restarts into metrics counter
	bus.Subscribe(events.TopicPluginStopped, func(_ events.Topic, _ interface{}) {
		met.IncPythonRestarts()
	})

	// --- Health checker ---
	addr := resolveAddr(cfg.API.Addr)
	checker := health.New(health.Config{
		PollInterval:     0, // use default 30s
		MaxMemoryMB:      512,
		MinDiskFreeBytes: 100 * 1024 * 1024,
		LogDir:           paths.LogDir(),
		APIAddr:          addr,
	}, gw, zl)

	shutCtx, shutStop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer shutStop()

	checker.Start(shutCtx)

	// --- API v1 ---
	srv := apiv1.New(
		apiv1.Config{Addr: addr, Version: "0.1.0"},
		tokenStore,
		gw,
		checker,
		met,
		zl,
	)
	go func() {
		if err := srv.ListenAndServe(); err != nil {
			zl.Info("api server exited", zap.Error(err))
		}
	}()

	// --- Tray (no-op in headless builds) ---
	port := portFromAddr(addr)
	trayHandler := tray.New(paths, port, "Cloud",
		func() { shutStop() },
		func() { gw.Restart() },
		zl,
	)
	checker.OnStatusChange(func(s health.OverallStatus) {
		trayHandler.SetStatus(s)
	})
	// tray.Run() must be on the main thread; launch everything else before this.
	// In headless mode, Run() returns immediately.
	go func() {
		<-shutCtx.Done()
		srv.Shutdown()
		gw.Stop()
	}()
	trayHandler.Run() // blocks on non-headless builds until Quit is clicked

	// Ensure we also exit on signal if tray exits first
	<-shutCtx.Done()
	zl.Info("navig-host stopped")
	return nil
}

// resolveAddr returns the configured addr, falling back to the next free port
// if the primary is already bound.
func resolveAddr(configured string) string {
	l, err := net.Listen("tcp", configured)
	if err == nil {
		_ = l.Close()
		return configured
	}
	// Primary port busy — pick an ephemeral one on 127.0.0.1
	l2, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return configured // give up, let the server fail with a clear error
	}
	addr := l2.Addr().String()
	_ = l2.Close()
	return addr
}

func portFromAddr(addr string) string {
	_, p, err := net.SplitHostPort(addr)
	if err != nil {
		return "4747"
	}
	return p
}
