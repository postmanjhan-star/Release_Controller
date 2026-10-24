"""Validate the configured Portainer resources before requesting a Git redeploy."""

import argparse
import errno
import json
import os
import socket
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class DeploymentError(RuntimeError):
    pass


class DeploymentOutcomeUnknown(DeploymentError):
    """The redeploy request may still be running after its response was lost."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def connection_failure_reason(exc: Exception) -> str:
    """Describe network failures without logging URLs or proxy credentials."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, socket.gaierror):
        return "DNS lookup failed"
    if isinstance(reason, TimeoutError) or getattr(reason, "errno", None) == errno.ETIMEDOUT:
        return "connection timed out"
    if isinstance(reason, ssl.SSLError):
        return "TLS handshake or certificate validation failed"
    code = getattr(reason, "errno", None)
    if code == errno.ECONNREFUSED:
        return "connection refused"
    if code in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return "network or host unreachable"
    if isinstance(reason, ConnectionResetError):
        return "connection reset"
    return "transport error"


class Portainer:
    def __init__(self, url: str, token: str, *, insecure: bool = False):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
        ):
            raise DeploymentError("portainer_url must be an HTTP(S) base URL without credentials.")
        self.base = url.rstrip("/")
        if not self.base.endswith("/api"):
            self.base += "/api"
        self.token = token
        self.context = ssl.create_default_context()
        if insecure:
            self.context.check_hostname = False
            self.context.verify_mode = ssl.CERT_NONE
        self.opener = build_opener(HTTPSHandler(context=self.context), NoRedirect())

    def request(self, method: str, path: str, payload: dict | None = None):
        request = Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"X-API-Key": self.token, "Content-Type": "application/json"},
            method=method,
        )
        timeout = 1800 if method == "PUT" else 30
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read()
        except HTTPError as exc:
            uncertain = method == "PUT" and exc.code in (502, 503, 504)
            # Stack responses contain environment secrets. Only print the API's
            # top-level error message, never entire responses or debug details.
            try:
                message = json.loads(exc.read()).get("message", "")
            except (ValueError, AttributeError):
                message = (
                    "Gateway did not return a JSON response."
                    if uncertain
                    else "Non-JSON response; check portainer_url and reverse proxy routing."
                )
            message = str(message).replace(self.token, "[redacted]")[:500]
            if uncertain:
                raise DeploymentOutcomeUnknown(
                    f"{method} {path}: HTTP {exc.code}: {message} "
                    "Portainer may still be deploying. Check the stack and Portainer logs before "
                    "retrying; the gateway timeout is independent of the client's 1800s timeout."
                ) from exc
            raise DeploymentError(f"{method} {path}: HTTP {exc.code}: {message}") from exc
        except (URLError, OSError) as exc:
            error = DeploymentOutcomeUnknown if method == "PUT" else DeploymentError
            raise error(
                f"{method} {path}: connection failed ({connection_failure_reason(exc)}; "
                f"timeout={timeout}s). Check build-container routing, firewall, proxy settings "
                "and Portainer logs before retrying."
            ) from exc
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as exc:
            if method == "PUT":
                raise DeploymentOutcomeUnknown(
                    f"{method} {path}: response was not JSON; Portainer may still be deploying. "
                    "Check the stack and Portainer logs before retrying."
                ) from exc
            raise DeploymentError(
                f"{method} {path}: expected JSON; check portainer_url and reverse proxy routing."
            ) from exc


def redeploy(environment, *, insecure: bool = False) -> None:
    names = ("PORTAINER_URL", "PORTAINER_API_TOKEN", "PORTAINER_STACK_ID", "PORTAINER_ENDPOINT_ID")
    values = {}
    for name in names:
        value = environment.get(name, "").strip()
        if not value:
            raise DeploymentError(f"Missing Drone secret: {name.lower()}")
        values[name] = value
    for name in ("PORTAINER_STACK_ID", "PORTAINER_ENDPOINT_ID"):
        value = values[name]
        if not value.isascii() or not value.isdecimal() or int(value) < 1:
            raise DeploymentError(f"{name.lower()} must be a positive numeric ID, not a name.")
    stack_id = int(values["PORTAINER_STACK_ID"])
    endpoint_id = int(values["PORTAINER_ENDPOINT_ID"])
    api = Portainer(values["PORTAINER_URL"], values["PORTAINER_API_TOKEN"], insecure=insecure)

    print(f"Checking Portainer environment {endpoint_id}...", flush=True)
    endpoint = api.request("GET", f"/endpoints/{endpoint_id}")
    if not isinstance(endpoint, dict) or endpoint.get("Id") != endpoint_id:
        raise DeploymentError("Portainer returned unexpected environment metadata.")
    print(f"Checking Portainer stack {stack_id}...", flush=True)
    stack = api.request("GET", f"/stacks/{stack_id}")
    if not isinstance(stack, dict) or stack.get("Id") != stack_id:
        raise DeploymentError("Portainer returned unexpected stack metadata.")
    if stack.get("EndpointId") not in (0, endpoint_id):
        raise DeploymentError(
            "Stack belongs to a different environment; check portainer_endpoint_id."
        )
    git = stack.get("GitConfig")
    if not isinstance(git, dict):
        raise DeploymentError("Stack is not a Git Stack; configure it from this Git repository.")
    reference = git.get("ReferenceName")
    if reference not in ("deploy", "refs/heads/deploy"):
        raise DeploymentError("Git Stack must track refs/heads/deploy; check its Git settings.")

    # Older Portainer versions overwrite Env and the Git ref when omitted.
    # Preserve them and keep the Git credentials already stored in Portainer.
    auth = git.get("Authentication")
    payload = {
        "Prune": False,
        "RepositoryReferenceName": reference,
        "Env": stack.get("Env") or [],
        "RepositoryAuthentication": isinstance(auth, dict),
        "RepositoryUsername": auth.get("Username", "") if isinstance(auth, dict) else "",
    }
    print("Requesting Portainer redeployment...", flush=True)
    api.request("PUT", f"/stacks/{stack_id}/git/redeploy?endpointId={endpoint_id}", payload)
    print("Portainer redeploy request completed.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--insecure", action="store_true", help="Skip TLS certificate verification."
    )
    args = parser.parse_args()
    try:
        redeploy(os.environ, insecure=args.insecure)
    except DeploymentError as exc:
        outcome = "outcome unknown" if isinstance(exc, DeploymentOutcomeUnknown) else "failed"
        print(f"Portainer deployment {outcome}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
