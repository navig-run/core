package ipc

import (
	"context"
	"sync"
	"time"
)

const HeartbeatInterval = 400 * time.Millisecond

type OperationScope struct {
	ctx     context.Context
	emitter Emitter
	evCtx   EventCtx
	hint    string

	done    chan struct{}
	once    sync.Once
	aborted bool
}

func NewOperationScope(ctx context.Context, emitter Emitter, evCtx EventCtx, hint string) *OperationScope {
	return &OperationScope{
		ctx:     ctx,
		emitter: emitter,
		evCtx:   evCtx,
		hint:    hint,
		done:    make(chan struct{}),
	}
}

func (s *OperationScope) WasAborted() bool {
	return s.aborted
}

func (s *OperationScope) Start() {
	start := time.Now()

	go func() {
		ticker := time.NewTicker(HeartbeatInterval)
		defer ticker.Stop()

		for {
			select {
			case <-s.ctx.Done():
				// Clean exit if context is cancelled, but mark aborted
				s.aborted = true
				s.End()
				return
			case <-s.done:
				// Clean exit via End()
				return
			case <-ticker.C:
				elapsed := time.Since(start).Milliseconds()
				s.emitter.Heartbeat(s.evCtx, HeartbeatData{
					State:   "busy",
					SinceMs: elapsed,
					Hint:    s.hint,
				})
			}
		}
	}()
}

func (s *OperationScope) End() {
	s.once.Do(func() {
		close(s.done)
	})
}
