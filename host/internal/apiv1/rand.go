package apiv1

import (
	"crypto/rand"
	"encoding/hex"
)

// fastRandBytes fills b with cryptographically random bytes.
// Unlike math/rand, this is safe and non-seeded.
func fastRandBytes(b []byte) error {
	_, err := rand.Read(b)
	return err
}

// NewRequestID returns a fresh 8-byte hex request identifier.
func NewRequestID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
