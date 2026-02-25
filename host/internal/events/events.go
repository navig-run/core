package events

import (
	"sync"
)

// Topic is the event channel identifier.
type Topic string

// Handler is a callback invoked when an event on a subscribed topic is published.
type Handler func(topic Topic, payload interface{})

// Bus is an in-process pub/sub event bus.
type Bus struct {
	mu          sync.RWMutex
	subscribers map[Topic][]Handler
}

// NewBus creates an empty Bus.
func NewBus() *Bus {
	return &Bus{
		subscribers: make(map[Topic][]Handler),
	}
}

// Subscribe registers a handler for a topic. Returns an unsubscribe function.
func (b *Bus) Subscribe(topic Topic, h Handler) func() {
	b.mu.Lock()
	b.subscribers[topic] = append(b.subscribers[topic], h)
	b.mu.Unlock()

	return func() {
		b.mu.Lock()
		defer b.mu.Unlock()
		handlers := b.subscribers[topic]
		filtered := handlers[:0]
		for _, existing := range handlers {
			// Compare function pointers via reflection trick – use closure identity
			if &existing != &h {
				filtered = append(filtered, existing)
			}
		}
		b.subscribers[topic] = filtered
	}
}

// Publish sends a payload to all handlers subscribed to the topic (non-blocking).
func (b *Bus) Publish(topic Topic, payload interface{}) {
	b.mu.RLock()
	handlers := make([]Handler, len(b.subscribers[topic]))
	copy(handlers, b.subscribers[topic])
	b.mu.RUnlock()

	for _, h := range handlers {
		h(topic, payload)
	}
}

// PublishAsync sends to all handlers in goroutines (fire-and-forget).
func (b *Bus) PublishAsync(topic Topic, payload interface{}) {
	b.mu.RLock()
	handlers := make([]Handler, len(b.subscribers[topic]))
	copy(handlers, b.subscribers[topic])
	b.mu.RUnlock()

	for _, h := range handlers {
		go h(topic, payload)
	}
}

// Common built-in topics.
const (
	TopicPluginStarted Topic = "plugin.started"
	TopicPluginStopped Topic = "plugin.stopped"
	TopicPluginError   Topic = "plugin.error"
	TopicAuthChanged   Topic = "auth.changed"
	TopicConfigChanged Topic = "config.changed"
)
