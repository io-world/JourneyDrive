from __future__ import annotations

import hashlib
import secrets
import socket
import ssl


class CertificateFingerprintMismatch(Exception):
    """The broker's presented certificate does not match the configured
    broker_cert_fingerprint. Never retried and never falls back to plaintext or an
    unpinned TLS connection — a mismatch is a configuration problem (wrong value
    copied, or a real man-in-the-middle), not a transient network blip."""


def normalize_fingerprint(raw: str) -> str:
    normalized = raw.replace(":", "").replace(" ", "").lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValueError("fingerprint must be a SHA-256 fingerprint: 64 hex characters, colons optional")
    return normalized


def fetch_pinned_ssl_context(host: str, port: int, expected_fingerprint: str, timeout: float = 10.0) -> ssl.SSLContext:
    """Fetch the certificate the broker presents at host:port, verify its SHA-256
    fingerprint matches expected_fingerprint, then return an SSLContext trusting
    *that exact certificate* for all real traffic. Raises CertificateFingerprintMismatch
    on a mismatch, before any application data (including credentials) is sent.

    This is deliberately not a per-connection verify callback — Python's stdlib ssl
    module doesn't expose one (that's OpenSSL's SSL_CTX_set_verify, never exposed to
    Python), and a callback wouldn't be safe for httpx anyway: httpx.AsyncClient
    manages its own connection pool (idle-timeout reconnects, retries) that
    application code can't intercept per-connection. Pinning the exact verified cert
    as the trust anchor instead means every TLS handshake this context is ever used
    for — including reconnects neither websockets' nor httpx's caller sees — is
    checked automatically by the standard chain-verification path.
    """
    expected = normalize_fingerprint(expected_fingerprint)

    probe_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    probe_context.check_hostname = False
    probe_context.verify_mode = ssl.CERT_NONE

    with socket.create_connection((host, port), timeout=timeout) as sock:
        # server_hostname is required by PROTOCOL_TLS_CLIENT for SNI even with
        # check_hostname disabled; the raw IP is connecting by IP, not a DNS name —
        # there's no name to verify against, only the pinned fingerprint below.
        with probe_context.wrap_socket(sock, server_hostname=host) as tls_sock:
            der_cert = tls_sock.getpeercert(binary_form=True)

    actual = hashlib.sha256(der_cert).hexdigest()
    if not secrets.compare_digest(actual, expected):
        raise CertificateFingerprintMismatch(
            f"broker at {host}:{port} presented a certificate with fingerprint {actual}, "
            f"expected {expected} — refusing to connect. Either the configured "
            "broker_cert_fingerprint is wrong, or the broker's certificate was "
            "regenerated without updating this client's config."
        )

    pem_cert = ssl.DER_cert_to_PEM_cert(der_cert)
    pinned_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    pinned_context.check_hostname = False
    pinned_context.verify_mode = ssl.CERT_REQUIRED
    pinned_context.load_verify_locations(cadata=pem_cert)
    return pinned_context
