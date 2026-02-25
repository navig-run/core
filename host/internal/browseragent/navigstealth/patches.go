// Package navigstealth provides Patchright-equivalent stealth patches
// for the NAVIG chromedp browser driver — pure Go, zero subprocess dependency.
//
// # What This Fixes
//
// chromedp (and any CDP-based driver) leaks automation fingerprints that
// bot-detection services check before serving page content. These patches
// remove all of them by injecting a script via
// Page.addScriptToEvaluateOnNewDocument — the same mechanism Patchright uses —
// which runs in every new document context *before* any page JavaScript.
//
// # Detectors Defeated
//
//   - navigator.webdriver → false                 (biggest leak)
//   - window.chrome missing in headless            → restored
//   - navigator.plugins empty in headless          → spoofed (3 real Chrome plugins)
//   - navigator.languages = []                     → ['en-US','en']
//   - Permissions API Notification probe            → patched
//   - screen.width/height = 0 in headless          → 1920×1080
//   - navigator.hardwareConcurrency / deviceMemory → realistic values
//   - iframe contentWindow.webdriver leak          → patched
//   - --enable-automation Chrome flag              → removed (via StealthFlags)
//
// # Usage
//
//	// 1. Add flags at allocator creation
//	opts := append(chromedp.DefaultExecAllocatorOptions[:], navigstealth.StealthFlags()...)
//	allocCtx, cancel := chromedp.NewExecAllocator(ctx, opts...)
//
//	// 2. Inject the script bundle once per browser context
//	browserCtx, cancel := chromedp.NewContext(allocCtx)
//	if err := chromedp.Run(browserCtx, navigstealth.Inject()); err != nil { ... }
//
//	// 3. All navigations on this context are now stealth-patched
package navigstealth

import (
	"context"
	"fmt"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

// ─────────────────────────────────────────────────────────────────────────────
// Launch flags
// ─────────────────────────────────────────────────────────────────────────────

// StealthFlags returns chromedp ExecAllocator options that remove Chrome's
// automation flags. Apply these *in addition to* DefaultExecAllocatorOptions.
func StealthFlags() []chromedp.ExecAllocatorOption {
	return []chromedp.ExecAllocatorOption{
		// Remove the blink-level webdriver flag — works even without the JS patch
		chromedp.Flag("disable-blink-features", "AutomationControlled"),
		// Turn off --enable-automation which chromedp adds via DefaultExecAllocatorOptions
		chromedp.Flag("enable-automation", false),
		// Restore popup + extension behaviour (removed by headless defaults)
		chromedp.Flag("disable-popup-blocking", false),
		chromedp.Flag("disable-component-update", false),
		chromedp.Flag("disable-default-apps", false),
		chromedp.Flag("disable-extensions", false),
		// Realistic 1080p window (headless defaults to 800×600)
		chromedp.WindowSize(1920, 1080),
		// Locale flags
		chromedp.Flag("lang", "en-US"),
		chromedp.Flag("accept-lang", "en-US,en;q=0.9"),
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// JS stealth bundle
// ─────────────────────────────────────────────────────────────────────────────

// stealthBundle is the NAVIG stealth JS bundle. Injected via
// Page.addScriptToEvaluateOnNewDocument so it runs before any page script.
const stealthBundle = `(function navig_stealth() {
  'use strict';

  // ── 1. navigator.webdriver = false ────────────────────────────────────────
  // The single most important patch. Checked by every bot detector.
  try {
    Object.defineProperty(navigator, 'webdriver', {
      get: () => false,
      configurable: true,
    });
  } catch(_) {}

  // ── 2. window.chrome = real-looking object ────────────────────────────────
  // Headless Chrome omits window.chrome. Real Chrome always has it.
  try {
    if (!window.chrome) {
      const chrome = {
        app: {
          isInstalled: false,
          getDetails: function() {},
          getIsInstalled: function() {},
          installState: function() {},
          runningState: function() {},
        },
        runtime: {
          PlatformOs: { MAC:'mac', WIN:'win', ANDROID:'android', CROS:'cros', LINUX:'linux', OPENBSD:'openbsd' },
          PlatformArch: { ARM:'arm', X86_32:'x86-32', X86_64:'x86-64' },
          PlatformNaclArch: { ARM:'arm', X86_32:'x86-32', X86_64:'x86-64' },
          RequestUpdateCheckStatus: { THROTTLED:'throttled', NO_UPDATE:'no_update', UPDATE_AVAILABLE:'update_available' },
          OnInstalledReason: { INSTALL:'install', UPDATE:'update', CHROME_UPDATE:'chrome_update', SHARED_MODULE_UPDATE:'shared_module_update' },
          OnRestartRequiredReason: { APP_UPDATE:'app_update', OS_UPDATE:'os_update', PERIODIC:'periodic' },
          connect: function() {},
          sendMessage: function() {},
        },
        loadTimes: function() {},
        csi: function() {},
      };
      try { Object.defineProperty(window, 'chrome', { value: chrome, writable: false, configurable: false }); }
      catch(_) { window.chrome = chrome; }
    }
  } catch(_) {}

  // ── 3. Realistic navigator.plugins (3 standard Chrome plugins) ────────────
  // Headless returns an empty PluginArray. Real Chrome always has ≥3.
  try {
    const pluginData = [
      { name:'Chrome PDF Plugin',  description:'Portable Document Format', filename:'internal-pdf-viewer',
        mimeTypes:[{ type:'application/x-google-chrome-pdf', suffixes:'pdf', description:'Portable Document Format' }] },
      { name:'Chrome PDF Viewer',  description:'', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',
        mimeTypes:[{ type:'application/pdf', suffixes:'pdf', description:'' }] },
      { name:'Native Client', description:'', filename:'internal-nacl-plugin',
        mimeTypes:[
          { type:'application/x-nacl',  suffixes:'',  description:'Native Client Executable' },
          { type:'application/x-pnacl', suffixes:'',  description:'Portable Native Client Executable' },
        ] },
    ];

    function mkPlugin(pd) {
      const plug = Object.create(Plugin.prototype);
      Object.defineProperties(plug, {
        name:        { value: pd.name,              enumerable: true },
        description: { value: pd.description,       enumerable: true },
        filename:    { value: pd.filename,           enumerable: true },
        length:      { value: pd.mimeTypes.length,  enumerable: true },
      });
      pd.mimeTypes.forEach((md, i) => {
        const mt = Object.create(MimeType.prototype);
        Object.defineProperties(mt, {
          type:          { value: md.type, enumerable: true },
          suffixes:      { value: md.suffixes, enumerable: true },
          description:   { value: md.description, enumerable: true },
          enabledPlugin: { value: plug, enumerable: true },
        });
        plug[i] = mt;
        plug[md.type] = mt;
      });
      return plug;
    }

    const plugins = pluginData.map(mkPlugin);
    const pArr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { pArr[i] = p; pArr[p.name] = p; });
    Object.defineProperty(pArr, 'length', { value: plugins.length, enumerable: true });
    Object.defineProperty(navigator, 'plugins', { get: () => pArr, configurable: true });
  } catch(_) {}

  // ── 4. navigator.languages ─────────────────────────────────────────────────
  try {
    Object.defineProperty(navigator, 'languages', {
      get: () => ['en-US', 'en'],
      configurable: true,
    });
  } catch(_) {}

  // ── 5. Permissions API — Notification probe ────────────────────────────────
  // Bot detectors call permissions.query({name:'notifications'}) and look for
  // the automatic 'denied' response that headless gives.
  try {
    if (navigator.permissions && navigator.permissions.query) {
      const _orig = navigator.permissions.query.bind(navigator.permissions);
      Object.defineProperty(navigator.permissions, 'query', {
        value: function(params) {
          if (params && params.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission, onchange: null });
          }
          return _orig(params);
        },
        configurable: true,
      });
    }
  } catch(_) {}

  // ── 6. Screen dimensions (headless reports 0×0) ────────────────────────────
  try {
    const screenProps = { width:1920, height:1080, availWidth:1920, availHeight:1040, colorDepth:24, pixelDepth:24 };
    for (const [k, v] of Object.entries(screenProps)) {
      Object.defineProperty(screen, k, { get: () => v, configurable: true });
    }
  } catch(_) {}

  // ── 7. Hardware fingerprint ────────────────────────────────────────────────
  try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true }); } catch(_) {}
  try { Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8, configurable: true }); } catch(_) {}

  // ── 8. iframe contentWindow.navigator.webdriver leak ──────────────────────
  // Some detectors create an iframe and read its inner navigator.
  try {
    const _origCW = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (_origCW) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
          const cw = _origCW.get.call(this);
          try {
            if (cw && cw.navigator) {
              Object.defineProperty(cw.navigator, 'webdriver', { get: () => false, configurable: true });
            }
          } catch(_) {}
          return cw;
        },
      });
    }
  } catch(_) {}

})();`

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

// Inject returns a chromedp.Action that registers the NAVIG stealth bundle
// via Page.addScriptToEvaluateOnNewDocument. Call this once per browser
// context, AFTER chromedp.NewContext but BEFORE any navigation.
//
//	browserCtx, cancel := chromedp.NewContext(allocCtx)
//	if err := chromedp.Run(browserCtx, navigstealth.Inject()); err != nil { ... }
func Inject() chromedp.Action {
	return chromedp.ActionFunc(func(ctx context.Context) error {
		_, err := page.AddScriptToEvaluateOnNewDocument(stealthBundle).Do(ctx)
		if err != nil {
			return fmt.Errorf("navigstealth: inject failed: %w", err)
		}
		return nil
	})
}

// InjectAndVerify is like Inject but also evaluates a quick self-test and
// returns an error if navigator.webdriver is still true after patching.
// Use in tests / debug sessions, not in production.
func InjectAndVerify() chromedp.Action {
	return chromedp.ActionFunc(func(ctx context.Context) error {
		if err := Inject().Do(ctx); err != nil {
			return err
		}
		var wd bool
		if err := chromedp.Evaluate(`navigator.webdriver`, &wd).Do(ctx); err != nil {
			return fmt.Errorf("navigstealth: verify eval failed: %w", err)
		}
		if wd {
			return fmt.Errorf("navigstealth: navigator.webdriver still true after patch")
		}
		return nil
	})
}
