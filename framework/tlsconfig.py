"""Enterprise TLS support: trust a company's ROOT certificate (and optionally a proxy) for outgoing HTTPS calls.

On many company networks HTTPS traffic is inspected by a proxy that re-signs every site with the company's own root
certificate. Programs that only trust the public certificate list they ship with (the AI SDKs use `certifi`) then fail with
CERTIFICATE_VERIFY_FAILED. This module builds one SSL context that trusts, together:
  1. the operating system's certificate store (on Windows: where IT installs the company root certificate),
  2. the public certificate list (certifi) so normal sites keep working,
  3. any extra root certificates you name (a .pem/.crt/.cer file, or a folder of them).

It is shared by the manager's AI assistant, the runner's AI element finder and rest_call.
Same idea and names as mom-mcp's ssl_ca_bundle_file / ssl_use_os_truststore. Settings come from the manager's Settings page
(workspace keys ssl_ca_bundle_file, ssl_use_os_truststore, proxy) or from environment variables:
  TESTBOT_SSL_CA_BUNDLE_FILE    path(s) of root certificate file(s)/folder(s), separated by ';'  (aliases: AI_CA_BUNDLE, SSL_CERT_FILE, REQUESTS_CA_BUNDLE)
  TESTBOT_SSL_USE_OS_TRUSTSTORE 1 (default) or 0                                                (alias: AI_USE_SYSTEM_CERTS)
  TESTBOT_PROXY                 proxy URL, e.g. http://proxy.corp:8080                          (aliases: AI_PROXY; HTTPS_PROXY is honoured automatically)"""
from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

CERT_SUFFIXES = {".pem", ".crt", ".cer", ".cert", ".der"}


class TlsConfigError(Exception):
    pass


@dataclass
class TlsSettings:
    ca_bundle: str = ""            # one or more paths separated by ';'
    use_system_certs: bool = True
    proxy: str = ""

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "TlsSettings":
        """Workspace/settings dictionary; the short keys ca_bundle / use_system_certs are accepted too."""
        d = d or {}
        bundle = d.get("ssl_ca_bundle_file") or d.get("ca_bundle") or ""
        system = d.get("ssl_use_os_truststore", d.get("use_system_certs", True))
        return cls(ca_bundle=str(bundle).strip(), use_system_certs=bool(system), proxy=str(d.get("proxy") or "").strip())

    @classmethod
    def from_env(cls) -> "TlsSettings":
        env = os.environ
        bundle = (env.get("TESTBOT_SSL_CA_BUNDLE_FILE") or env.get("AI_CA_BUNDLE") or env.get("SSL_CERT_FILE") or env.get("REQUESTS_CA_BUNDLE") or "")
        raw = env.get("TESTBOT_SSL_USE_OS_TRUSTSTORE", env.get("AI_USE_SYSTEM_CERTS", "1"))
        proxy = env.get("TESTBOT_PROXY") or env.get("AI_PROXY") or ""
        return cls(ca_bundle=bundle.strip(), use_system_certs=raw.strip().lower() not in ("0", "false", "no", "off"), proxy=proxy.strip())

    def merged_with_env(self) -> "TlsSettings":
        """Explicit settings win; anything left empty falls back to the environment variables."""
        env = TlsSettings.from_env()
        return TlsSettings(ca_bundle=self.ca_bundle or env.ca_bundle, use_system_certs=self.use_system_certs and env.use_system_certs, proxy=self.proxy or env.proxy)


def _paths(spec: str) -> list[Path]:
    return [Path(p.strip().strip('"')) for p in spec.replace(os.pathsep, ";").split(";") if p.strip()]


def _load_file(ctx: ssl.SSLContext, path: Path) -> int:
    raw = path.read_bytes()
    if b"-----BEGIN" in raw:
        text = raw.decode("ascii", errors="ignore")
        ctx.load_verify_locations(cadata=text)
        return text.count("-----BEGIN CERTIFICATE-----") + text.count("-----BEGIN TRUSTED CERTIFICATE-----")
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(raw))   # a binary .cer/.der export
    return 1


def load_extra_roots(ctx: ssl.SSLContext, spec: str) -> int:
    """Add the certificates named in `spec` to `ctx`. Returns how many were added; raises TlsConfigError if a path is bad."""
    total = 0
    for path in _paths(spec):
        if not path.exists():
            raise TlsConfigError(f"certificate path not found: {path}")
        files = [path] if path.is_file() else sorted(p for p in path.iterdir() if p.suffix.lower() in CERT_SUFFIXES)
        if not files:
            raise TlsConfigError(f"no certificate files (.pem .crt .cer .der) in folder {path}")
        for f in files:
            try:
                total += _load_file(ctx, f)
            except (ssl.SSLError, ValueError) as exc:
                raise TlsConfigError(f"{f.name} is not a valid certificate file ({exc})") from exc
    return total


def make_ssl_context(settings: Optional[TlsSettings] = None) -> ssl.SSLContext:
    s = (settings or TlsSettings()).merged_with_env()
    if s.use_system_certs:
        ctx = ssl.create_default_context()            # operating-system store (Windows: Trusted Root + Intermediate CAs)
    else:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())   # public CAs, so normal sites work whatever the OS store holds
    except ImportError:  # pragma: no cover
        pass
    if s.ca_bundle:
        load_extra_roots(ctx, s.ca_bundle)
    return ctx


def http_client(settings: Optional[TlsSettings] = None, timeout: float = 120.0, sdk: Optional[str] = None):
    """An HTTP client that trusts the settings above and uses the proxy (explicit, else HTTPS_PROXY from the environment).

    Pass sdk="anthropic" or "openai" to get the client class that SDK itself expects (SDK versions differ in the httpx
    build they use and reject a foreign client); without it a plain httpx.Client is returned."""
    s = (settings or TlsSettings()).merged_with_env()
    kwargs: dict[str, Any] = {"verify": make_ssl_context(s), "timeout": timeout}
    if s.proxy:
        kwargs["proxy"] = s.proxy
    if sdk == "anthropic":
        import anthropic

        return anthropic.DefaultHttpxClient(**kwargs)
    if sdk == "openai":
        import openai

        return openai.DefaultHttpxClient(**kwargs)
    import httpx

    return httpx.Client(**kwargs)


def client_args(settings: Optional[TlsSettings] = None) -> dict[str, Any]:
    """The same settings as keyword arguments for libraries that build their own httpx client (google-genai)."""
    s = (settings or TlsSettings()).merged_with_env()
    args: dict[str, Any] = {"verify": make_ssl_context(s)}
    if s.proxy:
        args["proxy"] = s.proxy
    return args


def env_for_child(settings: Optional[TlsSettings]) -> dict[str, str]:
    """Environment variables that make a child process (testbot.exe) use the same settings."""
    if settings is None:
        return {}
    out: dict[str, str] = {}
    if settings.ca_bundle:
        out["TESTBOT_SSL_CA_BUNDLE_FILE"] = settings.ca_bundle
    if not settings.use_system_certs:
        out["TESTBOT_SSL_USE_OS_TRUSTSTORE"] = "0"
    if settings.proxy:
        out["TESTBOT_PROXY"] = settings.proxy
    return out


HINT_CERT = ("The server's certificate is not trusted by this program. On a network that inspects HTTPS (a proxy that re-signs traffic) "
             "export your company's ROOT certificate (.pem/.cer), set its path as the certificate file, and keep 'Trust the operating system's "
             "certificate store' ticked. Your IT department can supply the root certificate.")
HINT_PROXY = "The proxy refused or could not be reached. Check the proxy address (http://host:port) and whether it needs a user name/password in the URL."
HINT_NET = "The address could not be reached. Check the network, the proxy setting, and that the endpoint is allowed by your firewall."


def explain_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if "certificate" in low or "ssl" in low:
        return f"{text}\n\n{HINT_CERT}"
    if "proxy" in low or "407" in low:
        return f"{text}\n\n{HINT_PROXY}"
    return f"{text}\n\n{HINT_NET}"


def test_connection(settings: Optional[TlsSettings], url: str, timeout: float = 15.0) -> dict[str, Any]:
    """HTTPS GET `url` with the settings. ANY http response (even 401/404) proves TLS and the proxy work."""
    try:
        client = http_client(settings, timeout=timeout)
    except TlsConfigError as exc:
        return {"ok": False, "message": str(exc), "url": url}
    try:
        r = client.get(url)
        return {"ok": True, "status": r.status_code, "url": url, "message": f"Connected securely (HTTP {r.status_code}).",
                "roots_from_file": len(settings.ca_bundle.split(";")) if settings and settings.ca_bundle else 0}
    except Exception as exc:  # noqa: BLE001 - reported with a hint
        return {"ok": False, "url": url, "message": explain_error(exc)}
    finally:
        client.close()
