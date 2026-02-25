package auth

import "github.com/zalando/go-keyring"

// KeyringStore implements SecretStore using the OS keychain.
type KeyringStore struct {
	service string
}

// NewKeyringStore creates a store backed by the OS keychain for the given service name.
func NewKeyringStore(service string) *KeyringStore {
	if service == "" {
		service = "navig-host"
	}
	return &KeyringStore{service: service}
}

func (s *KeyringStore) Get(key string) (string, error) {
	return keyring.Get(s.service, key)
}

func (s *KeyringStore) Set(key, value string) error {
	return keyring.Set(s.service, key, value)
}

func (s *KeyringStore) Delete(key string) error {
	return keyring.Delete(s.service, key)
}
