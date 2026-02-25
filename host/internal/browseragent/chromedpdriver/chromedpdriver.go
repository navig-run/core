package chromedpdriver

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

type Session struct {
	ID         string
	Ctx        context.Context
	Cancel     context.CancelFunc
	CancelImpl context.CancelFunc // for allocator
	Pages      map[string]*Page
	Stealth    bool
	mu         sync.Mutex
	TempDir    string // track temporary profiles for cleanup
}

type Page struct {
	ID        string
	SessionID string
	Ctx       context.Context
	Cancel    context.CancelFunc
}

type Driver struct {
	mu       sync.Mutex
	emitter  ipc.Emitter
	sessions map[string]*Session
}

func New(emitter ipc.Emitter) *Driver {
	return &Driver{
		emitter:  emitter,
		sessions: make(map[string]*Session),
	}
}

func (d *Driver) ListBrowsers() ([]browser.BrowserInstall, error) {
	scanner := browserscan.GetScanner()
	execs := scanner.Scan()

	var installs []browser.BrowserInstall
	for _, e := range execs {
		installs = append(installs, browser.BrowserInstall{
			Name: browser.BrowserName(e.Type),
			Path: e.Path,
		})
	}
	return installs, nil
}

func (d *Driver) Launch(config browser.SessionLaunchConfig) (*browser.SessionInfo, error) {
	evCtx := ipc.EventCtx{}
	scope := ipc.NewOperationScope(context.Background(), d.emitter, evCtx, "launching browser process")
	scope.Start()
	defer scope.End()

	d.emitter.Lifecycle(evCtx, ipc.LifecycleData{
		Event: "Launch",
	})

	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "scan_browsers",
		Message: "Scanning for browser installations",
	})

	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "ensure_profile",
		Message: fmt.Sprintf("Ensuring profile directory: %s", config.ProfileName),
	})

	var dir string
	var tempDirToClean string
	if config.ProfileName == "" {
		tmpDir, tmpErr := os.MkdirTemp("", "navig-chrome-*")
		if tmpErr != nil {
			return nil, fmt.Errorf("chromedpdriver: tmp dir: %w", tmpErr)
		}
		dir = tmpDir
		tempDirToClean = tmpDir
	} else {
		var err error
		dir, err = browser.ResolveProfileDir(config.ProfileName)
		if err != nil {
			return nil, err
		}
	}

	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "launch",
		Message: "Launching browser instance",
	})

	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.UserDataDir(dir),
	)

	// Inject custom executable path if found via scanner or requested
	if config.BrowserName == "auto" || config.BrowserName == "" {
		scanner := browserscan.GetScanner()
		execs := scanner.Scan()
		if len(execs) > 0 {
			// First available is preferred auto
			opts = append(opts, chromedp.ExecPath(execs[0].Path))
		}
	} else if config.BrowserName == "chrome" || config.BrowserName == "edge" {
		scanner := browserscan.GetScanner()
		execs := scanner.Scan()
		for _, e := range execs {
			if browser.BrowserName(e.Type) == config.BrowserName {
				opts = append(opts, chromedp.ExecPath(e.Path))
				break
			}
		}
	}

	if !config.Headless {
		opts = append(opts, chromedp.Flag("headless", false))
	} else {
		opts = append(opts, chromedp.Flag("headless", "new"))
	}

	// Stealth mode: inject NAVIG anti-detection flags
	// Activated when SessionLaunchConfig.DriverType == "stealth"
	stealth := config.DriverType == "stealth"
	if stealth {
		opts = append(opts, navigstealth.StealthFlags()...)
	}

	for _, arg := range config.Args {
		opts = append(opts, chromedp.Flag(arg, true))
	}

	allocCtx, cancelImpl := chromedp.NewExecAllocator(context.Background(), opts...)

	ctx, cancel := chromedp.NewContext(allocCtx)

	// This ensures the browser is launched immediately
	if err := chromedp.Run(ctx); err != nil {
		cancel()
		cancelImpl()
		return nil, fmt.Errorf("failed to start browser: %w", err)
	}

	d.mu.Lock()
	s := &Session{
		ID:         uuid.New().String(),
		Ctx:        ctx,
		Cancel:     cancel,
		CancelImpl: cancelImpl,
		Pages:      make(map[string]*Page),
		Stealth:    stealth,
		TempDir:    tempDirToClean,
	}
	d.sessions[s.ID] = s
	d.mu.Unlock()

	return &browser.SessionInfo{SessionId: s.ID}, nil
}

func (d *Driver) NewTab(config browser.NewTabConfig) (*browser.PageInfo, error) {
	d.mu.Lock()
	session, ok := d.sessions[config.SessionId]
	d.mu.Unlock()

	if !ok {
		return nil, fmt.Errorf("session not found")
	}

	pageCtx, pageCancel := chromedp.NewContext(session.Ctx)

	// Inject NAVIG stealth bundle before any navigation.
	// Page.addScriptToEvaluateOnNewDocument runs our patches in every new
	// document context, before any page JavaScript — same mechanism as Patchright.
	if session.Stealth {
		if err := chromedp.Run(pageCtx, navigstealth.Inject()); err != nil {
			pageCancel()
			return nil, fmt.Errorf("navigstealth inject failed: %w", err)
		}
	}

	// Open url if given
	if config.Url != "" {
		if err := chromedp.Run(pageCtx, chromedp.Navigate(config.Url)); err != nil {
			pageCancel()
			return nil, fmt.Errorf("failed to open tab: %w", err)
		}
	} else {
		if err := chromedp.Run(pageCtx); err != nil {
			pageCancel()
			return nil, fmt.Errorf("failed to open tab: %w", err)
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

func (d *Driver) findPage(pageId string) (*Page, error) {
	d.mu.Lock()
	defer d.mu.Unlock()

	for _, session := range d.sessions {
		session.mu.Lock()
		p, ok := session.Pages[pageId]
		session.mu.Unlock()
		if ok {
			return p, nil
		}
	}
	return nil, fmt.Errorf("page not found")
}

func (d *Driver) Goto(config browser.GotoConfig) (*browser.NavResult, error) {
	page, err := d.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}

	d.emitter.Lifecycle(evCtx, ipc.LifecycleData{
		Event: "Navigate",
		URL:   config.Url,
	})

	scope := ipc.NewOperationScope(page.Ctx, d.emitter, evCtx, "waiting for document.readyState")
	scope.Start()
	// Detect context cancellation on End() to emit aborted event
	defer func() {
		if scope.WasAborted() {
			d.emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: "SessionAborted"})
		}
		scope.End()
	}()

	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "navigate",
		Step:    "goto",
		Message: "Navigating to URL",
		Details: map[string]string{"url": config.Url},
	})

	var title string
	var urlStr string
	if err := chromedp.Run(page.Ctx,
		chromedp.Navigate(config.Url),
		chromedp.Title(&title),
		chromedp.Location(&urlStr),
	); err != nil {
		return nil, err
	}

	return &browser.NavResult{Url: urlStr, Title: title}, nil
}

func (d *Driver) Eval(config browser.EvalConfig) (*browser.EvalResult, error) {
	page, err := d.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	var res interface{}
	// NOTE: Timeout support can be added via context.WithTimeout
	if err := chromedp.Run(page.Ctx,
		chromedp.Evaluate(config.Js, &res),
	); err != nil {
		return nil, err
	}

	// Marshaling so we return raw message json.RawMessage
	bytes, err := json.Marshal(res)
	if err != nil {
		return nil, err
	}

	return &browser.EvalResult{Result: bytes}, nil
}

func (d *Driver) Screenshot(config browser.ScreenshotConfig) (*browser.ScreenshotResult, error) {
	page, err := d.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	scope := ipc.NewOperationScope(page.Ctx, d.emitter, evCtx, "capturing screenshot")
	scope.Start()
	defer scope.End()

	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "screenshot",
		Message: "Taking full page screenshot",
	})

	var buf []byte
	if err := chromedp.Run(page.Ctx,
		chromedp.FullScreenshot(&buf, 90),
	); err != nil {
		return nil, err
	}

	if err := os.WriteFile(config.Path, buf, 0644); err != nil {
		return nil, err
	}

	d.emitter.Artifact(evCtx, ipc.ArtifactData{
		Kind: "screenshot",
		Path: config.Path,
	})

	return &browser.ScreenshotResult{Path: config.Path}, nil
}

func (d *Driver) Close(config browser.CloseSessionConfig) error {
	d.mu.Lock()
	session, ok := d.sessions[config.SessionId]
	if ok {
		delete(d.sessions, config.SessionId)
	}
	d.mu.Unlock()

	if !ok {
		return fmt.Errorf("session not found")
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
