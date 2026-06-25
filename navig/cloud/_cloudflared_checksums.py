"""Pinned ``cloudflared`` release + SHA-256 digests per platform.

Bump CLOUDFLARED_VERSION and the per-asset hashes together. Source of truth:
<https://github.com/cloudflare/cloudflared/releases>.

If a release page does not publish digests next to the binaries, compute them
manually after download:

    python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <path>

The installer downloads the binary, verifies the SHA-256 against the value
here, and refuses to run if the hashes diverge -- this catches MITM / mirror
tampering before we hand a foreign binary control of a tunnel into the
machine.

Set the per-platform hash to the literal string ``UNPINNED`` to temporarily
allow installs while a release is being audited; the installer logs a loud
warning when an UNPINNED slot is used so it cannot ship silently.
"""

from __future__ import annotations

CLOUDFLARED_VERSION = "2024.11.1"

# Asset filename templates per platform×arch.
ASSETS: dict[tuple[str, str], str] = {
    ("linux", "x86_64"): "cloudflared-linux-amd64",
    ("linux", "amd64"): "cloudflared-linux-amd64",
    ("linux", "arm64"): "cloudflared-linux-arm64",
    ("linux", "aarch64"): "cloudflared-linux-arm64",
    ("darwin", "x86_64"): "cloudflared-darwin-amd64.tgz",
    ("darwin", "amd64"): "cloudflared-darwin-amd64.tgz",
    ("darwin", "arm64"): "cloudflared-darwin-amd64.tgz",
    ("windows", "x86_64"): "cloudflared-windows-amd64.exe",
    ("windows", "amd64"): "cloudflared-windows-amd64.exe",
}

# SHA-256 of each asset. Pinned by downloading the artifact from the GitHub
# release above and computing the digest locally. Set a slot to "UNPINNED" to
# temporarily allow installs while a release is being audited; the installer
# logs a loud warning when an UNPINNED slot is used.
CHECKSUMS: dict[str, str] = {
    "cloudflared-linux-amd64":        "55d789465955ccfffcd61ba72807a2a4495002f7d9b7cc5eadcaa1f93c279d25",
    "cloudflared-linux-arm64":        "84d1c367b48b91ece8b4f348a0fb0ff964d58840445d2d51ed3bfb92ea75d493",
    "cloudflared-darwin-amd64.tgz":   "691b5c319e02a0bd4d9d472f55464fc680b65261b967a3ec2d9a0cbba3893762",
    "cloudflared-windows-amd64.exe":  "9e53db3dba3bf7c8272454ae32d2704f82364f98d0346833682f628c25489b24",
}


def asset_name(os_name: str, arch: str) -> str | None:
    """Return the release asset filename for this platform, or None."""
    return ASSETS.get((os_name, arch))


def expected_sha256(asset: str) -> str | None:
    return CHECKSUMS.get(asset)


def release_url(asset: str) -> str:
    return (
        f"https://github.com/cloudflare/cloudflared/releases/download/"
        f"{CLOUDFLARED_VERSION}/{asset}"
    )
