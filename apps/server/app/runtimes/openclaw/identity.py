"""The host controller's per-instance Ed25519 device identity (protocol v3 proof)."""

import base64
import hashlib
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def new_device_key() -> str:
    return Ed25519PrivateKey.generate().private_bytes_raw().hex()


def device_id(key: str) -> str:
    public = (
        Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key)).public_key().public_bytes_raw()
    )
    return hashlib.sha256(public).hexdigest()


def sign_device(key: str, nonce: str, token: str, scopes: tuple[str, ...]) -> dict:
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key))
    public = private.public_key().public_bytes_raw()
    identifier = hashlib.sha256(public).hexdigest()
    signed_at = int(time.time() * 1000)
    payload = "|".join(
        [
            "v3",
            identifier,
            "gateway-client",
            "backend",
            "operator",
            ",".join(scopes),
            str(signed_at),
            token,
            nonce,
            "python",
            "",
        ]
    )
    return {
        "id": identifier,
        "publicKey": _b64(public),
        "signature": _b64(private.sign(payload.encode())),
        "signedAt": signed_at,
        "nonce": nonce,
    }
