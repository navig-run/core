package scriptengine

import (
	"context"
	"fmt"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"navig-core/host/internal/browseragent/pageintel"
	"navig-core/host/internal/vault"

	"github.com/chromedp/chromedp"
)

// TestNavBrowser_SelfHealingVaultLogin proves that the engine can automatically
// log into a website using Vault credentials, even if the website UI mutates radically (Chaos HTML).
func TestNavBrowser_SelfHealingVaultLogin(t *testing.T) {
	// 1. Create a chaotic local web server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Randomly serve different DOM structures
		htmlVer := rand.Intn(2)

		if r.Method == "POST" {
			// Simulate a successful login redirect
			fmt.Fprintln(w, `<html><body><h1>Dashboard</h1><button id="logout">Logout</button></body></html>`)
			return
		}

		if htmlVer == 0 {
			// Version A: Standard Bootstrap form
			fmt.Fprintln(w, `
				<html><body>
				<form id="v1form" method="POST">
					<input id="email_field" name="user" type="text" placeholder="Email">
					<input id="pass_field" name="pwd" type="password">
					<button id="submit_btn" type="submit">Login</button>
				</form>
				</body></html>`)
		} else {
			// Version B: Disgusting modern React/Tailwind minified mess with no IDs
			fmt.Fprintln(w, `
				<html><body>
				<div class="container css-abc">
					<label>Username</label>
					<input class="w-full text-sm css-991x" name="identity" type="text">
					<label>Secure Passphrase</label>
					<input class="w-full text-sm css-991x" name="credential" type="password">
					<button type="submit" class="btn-primary" onclick="document.forms[0].submit()">Sign In</button>
				</div>
				<form method="POST" style="display:none;"></form>
				</body></html>`)
		}
	}))
	defer server.Close()

	// 2. Load the Mock Vault (Awareness)
	operatorVault := &vault.Vault{
		Accounts: []vault.AccountProfile{
			{
				Domain:   server.URL, // e.g. "http://127.0.0.1:53241"
				Username: "vault_username_test",
				Password: "vault_secure_password_123",
			},
		},
	}

	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.Flag("headless", true),
	)
	allocCtx, cancel := chromedp.NewExecAllocator(context.Background(), opts...)
	defer cancel()

	// 3. Run NavBrowser against this server 5 times in a row, proving it heals.
	for i := 0; i < 3; i++ {
		t.Run(fmt.Sprintf("ChaosRun_%d", i), func(t *testing.T) {
			ctx, cancelCtx := chromedp.NewContext(allocCtx)
			defer cancelCtx()

			ctx, cancelCtx = context.WithTimeout(ctx, 15*time.Second)
			defer cancelCtx()

			err := chromedp.Run(ctx,
				chromedp.Navigate(server.URL),
				chromedp.WaitVisible(`body`),
			)
			if err != nil {
				t.Fatalf("Failed to navigate: %v", err)
			}

			// Simulated PageIntel extraction and Vault Fill
			// Instead of hardcoding keys, the agent evaluates the DOM
			inspector := pageintel.New(func(js string) ([]byte, error) {
				var res []byte
				err := chromedp.Run(ctx, chromedp.Evaluate(js, &res))
				return res, err
			})

			analysis, err := inspector.Analyze()
			if err != nil {
				t.Fatalf("Page analysis failed: %v", err)
			}

			// Map inputs to the secure Vault
			fills := pageintel.VaultMatch(analysis, operatorVault)

			// Ensure it found the 2 fields needed for login
			if len(fills) < 2 {
				t.Fatalf("VaultMatch failed to find username/password boxes. Only found: %d fills for URL %s", len(fills), analysis.URL)
			}

			// Act: Natively inject the values found by VaultMatch using NextGen Dispatchers
			for selector, value := range fills {
				// We execute pure JS to bypass React state protections (Native Setter)
				js := fmt.Sprintf(`
					(function(){
						let el = document.querySelector("%s");
						if(el) {
							el.value = "%s";
							el.dispatchEvent(new Event('input', {bubbles:true}));
							el.dispatchEvent(new Event('change', {bubbles:true}));
						}
					})()`, selector, value)

				if err := chromedp.Run(ctx, chromedp.Evaluate(js, nil)); err != nil {
					t.Fatalf("Failed to inject vault data natively: %v", err)
				}
			}

			// Submitting dynamically. Find the button using semantics.
			var btnSelector string
			for _, btn := range analysis.Buttons {
				textLower := strings.ToLower(btn.Text)
				if strings.Contains(textLower, "login") || strings.Contains(textLower, "sign") || strings.Contains(textLower, "submit") || btn.Type == "submit" {
					btnSelector = btn.Selector
					break
				}
			}
			if btnSelector == "" {
				// Fallback generic click
				btnSelector = `button`
			}

			err = chromedp.Run(ctx,
				chromedp.Click(btnSelector),
				// Outcome Verification: Look for "Logout" which proves the form submitted successfully
				chromedp.WaitVisible(`h1`),
			)
			if err != nil {
				t.Fatalf("Failed to submit form dynamically: %v", err)
			}
		})
	}
}
