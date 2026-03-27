// Package navbrowser is the NAVIG browser automation engine.
//
// NavBrowser is NAVIG's canonical Go browser driver — built on chromedp/CDP
// with integrated stealth patching, session isolation, and profile management.
//
// Previously called "chromedpdriver", renamed to reflect its place as a
// first-class component of the NAVIG ecosystem.
//
// Usage:
//
//	engine := navbrowser.New(emitter)
//	session, _ := engine.Launch(browser.SessionLaunchConfig{DriverType: "stealth"})
//	page,    _ := engine.NewTab(browser.NewTabConfig{SessionId: session.SessionId})
//	engine.Goto(browser.GotoConfig{PageId: page.PageId, Url: "https://example.com"})
package navbrowser

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sync"

	"github.com/google/uuid"

	"github.com/chromedp/chromedp"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/browserscan"
	"navig-core/host/internal/browseragent/ipc"
	"navig-core/host/internal/browseragent/navigstealth"
)

// Session represents a running browser instance managed by NavBrowser.
type Session struct {
	ID         string
	Ctx        context.Context
	Cancel     context.CancelFunc
	CancelImpl context.CancelFunc // for allocator
	Pages      map[string]*Page
	Stealth    bool // true = navigstealth active
	mu         sync.Mutex
	TempDir    string // track temporary profiles for cleanup
}

// Page represents a single browser tab within a Session.
type Page struct {
	ID        string
	SessionID string
	Ctx       context.Context
	Cancel    context.CancelFunc
}

// NavBrowser is the NAVIG browser engine — chromedp-based with optional stealth mode.
type NavBrowser struct {
	mu       sync.Mutex
	emitter  ipc.Emitter
	sessions map[string]*Session
}

// noopEmitter is a null-object implementation of ipc.Emitter used when
// no real emitter is provided (e.g., in tests or CLI invocations).
type noopEmitter struct{}

func (noopEmitter) WriteJSONLine(_ interface{})                        {}
func (noopEmitter) Lifecycle(_ ipc.EventCtx, _ ipc.LifecycleData)     {}
func (noopEmitter) Status(_ ipc.EventCtx, _ string, _ ipc.StatusData) {}
func (noopEmitter) Heartbeat(_ ipc.EventCtx, _ ipc.HeartbeatData)     {}
func (noopEmitter) Artifact(_ ipc.EventCtx, _ ipc.ArtifactData)       {}
func (noopEmitter) Error(_ ipc.EventCtx, _ ipc.ErrorData)             {}

// New creates a NavBrowser instance. The emitter receives IPC lifecycle events.
// Pass nil to use a no-op emitter (safe for tests and direct CLI use).
func New(emitter ipc.Emitter) *NavBrowser {
	if emitter == nil {
		emitter = noopEmitter{}
	}
	return &NavBrowser{
		emitter:  emitter,
		sessions: make(map[string]*Session),
	}
}

// ListBrowsers returns all detected browser installations on this machine.
func (nb *NavBrowser) ListBrowsers() ([]browser.BrowserInstall, error) {
	scanner := browserscan.GetScanner()
	var installs []browser.BrowserInstall
	for _, e := range scanner.Scan() {
		installs = append(installs, browser.BrowserInstall{
			Name: browser.BrowserName(e.Type),
			Path: e.Path,
		})
	}
	return installs, nil
}

// Launch starts a new browser process and returns a Session.
// Set config.DriverType = "stealth" to activate NAVIG stealth patches.
func (nb *NavBrowser) Launch(config browser.SessionLaunchConfig) (*browser.SessionInfo, error) {
	evCtx := ipc.EventCtx{}
	scope := ipc.NewOperationScope(context.Background(), nb.emitter, evCtx, "launching NavBrowser")
	scope.Start()
	defer scope.End()

	nb.emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: "NavBrowserLaunch"})
	nb.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "launch",
		Message: "Starting NavBrowser engine",
	})

	// Resolve user-data-dir:
	var dir string
	var tempDirToClean string
	if config.ProfileName == "" {
		tmpDir, tmpErr := os.MkdirTemp("", "navig-chrome-*")
		if tmpErr != nil {
			return nil, fmt.Errorf("navbrowser: tmp dir: %w", tmpErr)
		}
		dir = tmpDir
		tempDirToClean = tmpDir
	} else {
		var resolveErr error
		dir, resolveErr = browser.ResolveProfileDir(config.ProfileName)
		if resolveErr != nil {
			return nil, fmt.Errorf("navbrowser: profile dir: %w", resolveErr)
		}
	}

	stealth := config.DriverType == string(browser.DriverStealth)

	// Base allocator options — start from a clean set to avoid flag conflicts
	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.UserDataDir(dir),
		chromedp.Flag("disable-dev-shm-usage", true), // required for Docker/CI
		chromedp.Flag("no-sandbox", true),
		chromedp.Flag("disable-background-networking", true),
		chromedp.Flag("disable-client-side-phishing-detection", true),
	)

	// Stealth mode: apply NAVIG anti-detection flags before anything else
	if stealth {
		opts = append(opts, navigstealth.StealthFlags()...)
		nb.emitter.Status(evCtx, "info", ipc.StatusData{
			Phase:   "stealth",
			Message: "NavBrowser stealth mode active — anti-detection patches applied",
		})
	}

	// Headless mode: use --headless (stable, works across Chrome 90+)
	// --headless=new requires Chrome 112+ and may fail on older or restricted installs.
	if !config.Headless {
		opts = append(opts,
			chromedp.Flag("headless", false),
			chromedp.Flag("disable-gpu", false),
		)
	}
	// When Headless=true: DefaultExecAllocatorOptions already sets --headless + --disable-gpu.

	// Browser binary selection
	nb.emitter.Status(evCtx, "info", ipc.StatusData{Phase: "scan_browsers", Message: "Scanning for browser installations"})
	if config.BrowserName == "auto" || config.BrowserName == "" {
		scanner := browserscan.GetScanner()
		if execs := scanner.Scan(); len(execs) > 0 {
			opts = append(opts, chromedp.ExecPath(execs[0].Path))
		}
	} else if config.BrowserName == "chrome" || config.BrowserName == "edge" {
		scanner := browserscan.GetScanner()
		for _, e := range scanner.Scan() {
			if browser.BrowserName(e.Type) == config.BrowserName {
				opts = append(opts, chromedp.ExecPath(e.Path))
				break
			}
		}
	}

	// Extra args
	for _, arg := range config.Args {
		opts = append(opts, chromedp.Flag(arg, true))
	}

	allocCtx, cancelImpl := chromedp.NewExecAllocator(context.Background(), opts...)
	ctx, cancel := chromedp.NewContext(allocCtx)

	if err := chromedp.Run(ctx); err != nil {
		cancel()
		cancelImpl()
		return nil, fmt.Errorf("navbrowser: launch failed: %w", err)
	}

	nb.mu.Lock()
	s := &Session{
		ID:         uuid.New().String(),
		Ctx:        ctx,
		Cancel:     cancel,
		CancelImpl: cancelImpl,
		Pages:      make(map[string]*Page),
		Stealth:    stealth,
		TempDir:    tempDirToClean,
	}
	nb.sessions[s.ID] = s
	nb.mu.Unlock()

	return &browser.SessionInfo{SessionId: s.ID}, nil
}

// NewTab opens a new browser tab within an existing session.
// In stealth sessions, the NAVIG JS patch bundle is injected before navigation.
func (nb *NavBrowser) NewTab(config browser.NewTabConfig) (*browser.PageInfo, error) {
	nb.mu.Lock()
	session, ok := nb.sessions[config.SessionId]
	nb.mu.Unlock()
	if !ok {
		return nil, fmt.Errorf("navbrowser: session not found: %s", config.SessionId)
	}

	pageCtx, pageCancel := chromedp.NewContext(session.Ctx)

	// Inject NAVIG stealth bundle via Page.addScriptToEvaluateOnNewDocument.
	// This runs our anti-detection JS before any page script in every new document.
	if session.Stealth {
		if err := chromedp.Run(pageCtx, navigstealth.Inject()); err != nil {
			pageCancel()
			return nil, fmt.Errorf("navbrowser: stealth inject failed: %w", err)
		}
	}

	if config.Url != "" {
		if err := chromedp.Run(pageCtx, chromedp.Navigate(config.Url)); err != nil {
			pageCancel()
			return nil, fmt.Errorf("navbrowser: new tab navigate: %w", err)
		}
	} else {
		if err := chromedp.Run(pageCtx); err != nil {
			pageCancel()
			return nil, fmt.Errorf("navbrowser: new tab init: %w", err)
		}
	}

	page := &Page{
		ID:        uuid.New().String(),
		SessionID: config.SessionId,
		Ctx:       pageCtx,
		Cancel:    pageCancel,
	}

	session.mu.Lock()
	session.Pages[page.ID] = page
	session.mu.Unlock()

	return &browser.PageInfo{PageId: page.ID}, nil
}

func (nb *NavBrowser) findPage(pageID string) (*Page, error) {
	nb.mu.Lock()
	defer nb.mu.Unlock()
	for _, session := range nb.sessions {
		session.mu.Lock()
		p, ok := session.Pages[pageID]
		session.mu.Unlock()
		if ok {
			return p, nil
		}
	}
	return nil, fmt.Errorf("navbrowser: page not found: %s", pageID)
}

// Goto navigates a page to a URL, returning final URL and title.
func (nb *NavBrowser) Goto(config browser.GotoConfig) (*browser.NavResult, error) {
	page, err := nb.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	nb.emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: "Navigate", URL: config.Url})

	scope := ipc.NewOperationScope(page.Ctx, nb.emitter, evCtx, "waiting for document.readyState")
	scope.Start()
	defer func() {
		if scope.WasAborted() {
			nb.emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: "SessionAborted"})
		}
		scope.End()
	}()

	var title, urlStr string
	if err := chromedp.Run(page.Ctx,
		chromedp.Navigate(config.Url),
		chromedp.Title(&title),
		chromedp.Location(&urlStr),
	); err != nil {
		return nil, fmt.Errorf("navbrowser: goto %s: %w", config.Url, err)
	}

	return &browser.NavResult{Url: urlStr, Title: title}, nil
}

// Eval executes JavaScript in the context of a page and returns the result.
func (nb *NavBrowser) Eval(config browser.EvalConfig) (*browser.EvalResult, error) {
	page, err := nb.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	var res interface{}
	if err := chromedp.Run(page.Ctx,
		chromedp.Evaluate(config.Js, &res),
	); err != nil {
		return nil, fmt.Errorf("navbrowser: eval: %w", err)
	}

	b, err := json.Marshal(res)
	if err != nil {
		return nil, fmt.Errorf("navbrowser: eval marshal: %w", err)
	}

	return &browser.EvalResult{Result: b}, nil
}

// Screenshot captures a full-page screenshot and saves it to config.Path.
func (nb *NavBrowser) Screenshot(config browser.ScreenshotConfig) (*browser.ScreenshotResult, error) {
	page, err := nb.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	scope := ipc.NewOperationScope(page.Ctx, nb.emitter, evCtx, "capturing screenshot")
	scope.Start()
	defer scope.End()

	var buf []byte
	if err := chromedp.Run(page.Ctx,
		chromedp.FullScreenshot(&buf, 90),
	); err != nil {
		return nil, fmt.Errorf("navbrowser: screenshot: %w", err)
	}

	if err := os.WriteFile(config.Path, buf, 0644); err != nil {
		return nil, fmt.Errorf("navbrowser: screenshot write: %w", err)
	}

	nb.emitter.Artifact(evCtx, ipc.ArtifactData{Kind: "screenshot", Path: config.Path})
	return &browser.ScreenshotResult{Path: config.Path}, nil
}

// Close terminates a browser session and all its pages.
func (nb *NavBrowser) Close(config browser.CloseSessionConfig) error {
	nb.mu.Lock()
	session, ok := nb.sessions[config.SessionId]
	if ok {
		delete(nb.sessions, config.SessionId)
	}
	nb.mu.Unlock()
	if !ok {
		return fmt.Errorf("navbrowser: session not found: %s", config.SessionId)
	}

	session.mu.Lock()
	for _, p := range session.Pages {
		p.Cancel()
	}
	session.mu.Unlock()

	session.Cancel()
	session.CancelImpl()

	if session.TempDir != "" {
		_ = os.RemoveAll(session.TempDir)
	}
	return nil
}
