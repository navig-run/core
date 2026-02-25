package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/ipc"
	"navig-core/host/internal/browseragent/navbrowser"
	"navig-core/host/internal/browseragent/playwrightdriver"
	"navig-core/host/internal/browseragent/profilemgr"
	"navig-core/host/internal/browseragent/router"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--version" {
		fmt.Println("navig-browser-agent v0.1.0")
		return
	}

	mu := &sync.Mutex{}
	emitter := ipc.NewStdoutEmitter(mu)

	navBrowser := navbrowser.New(emitter)
	playwrightDriverInstance := playwrightdriver.New(emitter)

	registry, err := profilemgr.NewRegistry()
	if err != nil {
		fmt.Printf("Startup error: failed to initialize profile registry: %v\n", err)
		os.Exit(1)
	}

	taskRouter := router.New(
		registry,
		func(e interface{}) browser.Driver { return navBrowser },
		func(e interface{}) browser.Driver { return playwrightDriverInstance },
	)


	server := ipc.New(emitter)
	ipc.RegisterTaskHandlers(server, taskRouter)
	ipc.RegisterA11yHandlers(server, navBrowser) // Browser.AriaSnapshot, .Click, .Fill

	server.Handlers["Browser.ListBrowsers"] = func(_ json.RawMessage) (interface{}, error) {
		return navBrowser.ListBrowsers()
	}

	server.Handlers["Session.Launch"] = func(params json.RawMessage) (interface{}, error) {
		var cfg browser.SessionLaunchConfig
		if err := json.Unmarshal(params, &cfg); err != nil {
			return nil, err
		}

		var activeDriver browser.Driver

		switch cfg.DriverType {
		case "playwright":
			// Try playwright stub; falls back to chromedp on ErrEngineNotInstalled
			res, err := playwrightDriverInstance.Launch(cfg)
			if errors.Is(err, browser.ErrEngineNotInstalled) {
				emitter.Lifecycle(ipc.EventCtx{}, ipc.LifecycleData{
					Event:    "DriverFallback",
					Fallback: "chromedp",
				})
				activeDriver = navBrowser
			} else {
				return res, err
			}
		case "stealth":
			// Stealth = navBrowser + NAVIG stealth patches.
			activeDriver = navBrowser
		default:
			activeDriver = navBrowser
		}

		// Inject CDPDebugPort flag if requested — Python CDPBridge uses this to attach.
		if cfg.CDPDebugPort > 0 {
			cfg.Args = append(cfg.Args,
				fmt.Sprintf("--remote-debugging-port=%d", cfg.CDPDebugPort),
				"--remote-debugging-address=127.0.0.1",
			)
		}

		return activeDriver.Launch(cfg)
	}


	server.Handlers["Page.NewTab"] = func(params json.RawMessage) (interface{}, error) {
		var cfg browser.NewTabConfig
		if err := json.Unmarshal(params, &cfg); err != nil {
			return nil, err
		}
		// In a multi-engine environment, this would ideally route to the driver governing the active session.
		// For the scope of this step, we maintain chromedp as the executing body, assuming isolation or single-engine-per-sidecar mode.
		return navBrowser.NewTab(cfg)
	}

	server.Handlers["Page.Goto"] = func(params json.RawMessage) (interface{}, error) {
		var cfg browser.GotoConfig
		if err := json.Unmarshal(params, &cfg); err != nil {
			return nil, err
		}
		return navBrowser.Goto(cfg)
	}

	server.Handlers["Page.Eval"] = func(params json.RawMessage) (interface{}, error) {
		var cfg browser.EvalConfig
		if err := json.Unmarshal(params, &cfg); err != nil {
			return nil, err
		}
		return navBrowser.Eval(cfg)
	}

	server.Handlers["Page.Screenshot"] = func(params json.RawMessage) (interface{}, error) {
		var cfg browser.ScreenshotConfig
		if err := json.Unmarshal(params, &cfg); err != nil {
			return nil, err
		}
		return navBrowser.Screenshot(cfg)
	}

	server.Handlers["Session.Close"] = func(params json.RawMessage) (interface{}, error) {
		var cfg browser.CloseSessionConfig
		if err := json.Unmarshal(params, &cfg); err != nil {
			return nil, err
		}
		return nil, navBrowser.Close(cfg)
	}

	if err := server.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "server error: %v\n", err)
		os.Exit(1)
	}
}
