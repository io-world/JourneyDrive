from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from journeydrive_windows_thinclient.tls_pinning import (
    CertificateFingerprintMismatch,
    fetch_pinned_ssl_context,
    normalize_fingerprint,
)


def test_normalize_fingerprint_strips_colons_and_lowercases() -> None:
    bare = "ab" * 32
    with_colons = ":".join(bare[i : i + 2] for i in range(0, len(bare), 2)).upper()
    assert normalize_fingerprint(with_colons) == bare
    assert normalize_fingerprint(bare) == bare


def test_normalize_fingerprint_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="64 hex characters"):
        normalize_fingerprint("ab:cd")


def test_normalize_fingerprint_rejects_non_hex() -> None:
    with pytest.raises(ValueError, match="64 hex characters"):
        normalize_fingerprint("zz" * 32)


def test_normalize_fingerprint_accepts_bare_hex() -> None:
    assert normalize_fingerprint("ab" * 32) == "ab" * 32


requires_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")


@pytest.fixture
def self_signed_cert(tmp_path: Path) -> tuple[Path, Path, str]:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "1", "-nodes",
            "-keyout", str(key_path), "-out", str(cert_path),
            "-subj", "/CN=test", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    fingerprint_out = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # "SHA256 Fingerprint=AA:BB:...\n"
    fingerprint = fingerprint_out.split("=", 1)[1].strip()
    return cert_path, key_path, fingerprint


class _TlsProbeServer:
    """Minimal synchronous TLS server for testing fetch_pinned_ssl_context against
    a real socket, without needing an event loop (fetch_pinned_ssl_context itself
    is blocking, by design — see tls_pinning.py)."""

    def __init__(self, cert_path: Path, key_path: Path) -> None:
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self._sock.settimeout(0.5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            try:
                with self._context.wrap_socket(conn, server_side=True):
                    pass
            except ssl.SSLError:
                pass

    def __enter__(self) -> "_TlsProbeServer":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


@requires_openssl
def test_fetch_pinned_ssl_context_succeeds_with_correct_fingerprint(self_signed_cert) -> None:
    cert_path, key_path, fingerprint = self_signed_cert
    with _TlsProbeServer(cert_path, key_path) as server:
        context = fetch_pinned_ssl_context("127.0.0.1", server.port, fingerprint)
        assert isinstance(context, ssl.SSLContext)

        # The returned context should actually work for a real subsequent connection.
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname="127.0.0.1") as tls_sock:
                assert tls_sock.version() is not None


@requires_openssl
def test_fetch_pinned_ssl_context_rejects_wrong_fingerprint(self_signed_cert) -> None:
    cert_path, key_path, _fingerprint = self_signed_cert
    wrong_fingerprint = "00" * 32
    with _TlsProbeServer(cert_path, key_path) as server:
        with pytest.raises(CertificateFingerprintMismatch, match="refusing to connect"):
            fetch_pinned_ssl_context("127.0.0.1", server.port, wrong_fingerprint)
