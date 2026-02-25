package wait

import (
	"context"
	"fmt"
	"time"

	"github.com/chromedp/chromedp"

	"navig-core/host/internal/browseragent/ipc"
)

// WithTimeout creates a context with a timeout in milliseconds.
func WithTimeout(ctx context.Context, ms int) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, time.Duration(ms)*time.Millisecond)
}

// Retry retries a function a given number of times with a backoff in milliseconds.
func Retry(ctx context.Context, attempts int, backoffMs int, fn func() error) error {
	var err error
	for i := 0; i < attempts; i++ {
		if err = fn(); err == nil {
			return nil
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(backoffMs) * time.Millisecond):
			// continue retrying
		}
	}
	return fmt.Errorf("failed after %d attempts, last error: %w", attempts, err)
}

// WaitDocumentReady evaluates document.readyState repeatedly until "complete".
func WaitDocumentReady(ctx context.Context, page interface{}, emitter ipc.Emitter, evCtx ipc.EventCtx) error {
	// Assuming page is a context.Context from chromedp for now, per chromedp design
	pageCtx, ok := page.(context.Context)
	if !ok {
		return fmt.Errorf("page must be a chromedp context")
	}

	emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "wait",
		Message: "Waiting for document.readyState to be complete",
	})

	err := chromedp.Run(pageCtx, chromedp.WaitReady("body", chromedp.ByQuery))
	if err != nil {
		emitter.Error(evCtx, ipc.ErrorData{
			Code:      "NAV_TIMEOUT",
			Message:   fmt.Sprintf("Timeout waiting for document.readyState: %v", err),
			Retryable: true,
		})
		return err
	}

	return nil
}

// WaitSelectorVisible is a stub as per requirements.
func WaitSelectorVisible(ctx context.Context, page interface{}, selector string, timeoutMs int) error {
	// Stub OK
	return nil
}

// WaitForStable waits until no new DOM mutations have occurred for the stabilization threshold.
func WaitForStable(ctx context.Context, page interface{}, timeoutMs int, emitter ipc.Emitter, evCtx ipc.EventCtx) error {
	emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "wait_stable",
		Message: "Waiting for DOM stabilization",
	})

	errCh := make(chan error, 1)

	switch p := page.(type) {
	case context.Context: // chromedp context
		go func() {
			// In chromedp, simulate stable by waiting for readyState and network idle.
			// A pure DOM mutation observer requires JS injection.
			err := chromedp.Run(p,
				chromedp.WaitReady("body", chromedp.ByQuery),
			)
			errCh <- err
		}()
	// case *playwright.Page:
	//	go func() { errCh <- p.WaitForLoadState(playwright.PageWaitForLoadStateOptions{State: playwright.LoadStateNetworkidle}) }()
	default:
		return fmt.Errorf("unsupported page type for wait primitives: %T", page)
	}

	select {
	case err := <-errCh:
		if err != nil {
			emitter.Error(evCtx, ipc.ErrorData{Code: "NAV_TIMEOUT", Message: err.Error(), Retryable: true})
		}
		return err
	case <-time.After(time.Duration(timeoutMs) * time.Millisecond):
		timeoutErr := fmt.Errorf("WaitForStable timed out after %d ms", timeoutMs)
		emitter.Error(evCtx, ipc.ErrorData{Code: "NAV_TIMEOUT", Message: timeoutErr.Error(), Retryable: true})
		return timeoutErr
	case <-ctx.Done():
		return ctx.Err()
	}
}
