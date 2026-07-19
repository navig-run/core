"""
Regression: ConnectionStore.read_secret must REVEAL the vault secret, never
return the masked '***'.

The vault wraps secrets in navig's SecretStr, whose __str__/__repr__ mask to
'***' and which exposes plaintext via .reveal() (NOT pydantic's
.get_secret_value()). read_secret previously checked only get_secret_value and
fell back to str(), silently corrupting every stored-secret connection's
credential to '***' — which made claude-max (and custom endpoints) fail
validation as "Invalid API key" / needs_reauth.
"""

from __future__ import annotations

from navig.providers.connections import ConnectionStore
from navig.vault import SecretStr


class RevealVault:
    """Vault stand-in that returns navig SecretStr (masks under str/repr)."""

    def __init__(self):
        self.items: dict[str, bytes] = {}

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload
        return "id-" + label

    def get_secret(self, label):
        if label not in self.items:
            raise KeyError(label)
        data = self.items[label]
        text = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
        return SecretStr(text)

    def delete(self, label):
        return self.items.pop(label, None) is not None


class PydanticStyleVault(RevealVault):
    """A vault whose wrapper exposes get_secret_value() (pydantic-style)."""

    class _S:
        def __init__(self, v):
            self._v = v

        def get_secret_value(self):
            return self._v

        def __str__(self):
            return "**********"

    def get_secret(self, label):
        if label not in self.items:
            raise KeyError(label)
        data = self.items[label]
        text = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
        return self._S(text)


def test_read_secret_reveals_navig_secretstr(tmp_path):
    store = ConnectionStore(tmp_path / "c.db", vault=RevealVault())
    token = "sk-ant-oat01-REAL-TOKEN-abcdef0123456789"
    ref = store.store_secret("claude-max", token, provider="claude-max")

    got = store.read_secret(ref)
    assert got == token
    assert got != "***"  # the exact regression


def test_read_secret_handles_pydantic_style(tmp_path):
    store = ConnectionStore(tmp_path / "c.db", vault=PydanticStyleVault())
    ref = store.store_secret("openai-api", "sk-openai-xyz", provider="openai-api")
    assert store.read_secret(ref) == "sk-openai-xyz"


def test_read_secret_none_for_missing(tmp_path):
    store = ConnectionStore(tmp_path / "c.db", vault=RevealVault())
    assert store.read_secret(None) is None
    assert store.read_secret("connection/claude-max/missing") is None
