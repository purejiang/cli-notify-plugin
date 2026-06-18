"""E2EE Encrypt/Decrypt: ECDH P-256 + AES-256-GCM + HKDF-SHA256.

Uses HKDF info string 'cli-notify-v2' to derive the shared key.
"""

import base64
import json
import os
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend

# HKDF parameters — must match Android/Relay side
HKDF_INFO = b"cli-notify-v2"
HKDF_SALT = b"\x00" * 32


def encrypt_envelope(envelope: dict, peer_public_key_b64: str) -> dict:
    """Encrypt envelope.data in-place, replacing it with an EncryptedPayload.

    Generates an ephemeral ECDH P-256 key pair, performs key agreement
    with the peer's public key, derives an AES-256-GCM key via HKDF-SHA256,
    and replaces envelope["data"] with {"ephemeralKey", "iv", "ciphertext"}.

    Args:
        envelope: The full envelope dict (modified in place).
        peer_public_key_b64: Base64-encoded 65-byte uncompressed P-256 public key.

    Returns:
        The modified envelope with encrypted data.
    """
    # Generate ephemeral P-256 key pair
    eph_priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
    eph_pub = eph_priv.public_key()
    eph_pub_bytes = eph_pub.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    # Parse peer's public key
    peer_pub_bytes = base64.b64decode(peer_public_key_b64)
    peer_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), peer_pub_bytes
    )

    # ECDH shared secret
    shared = eph_priv.exchange(ec.ECDH(), peer_pub)

    # HKDF-SHA256 key derivation
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=HKDF_SALT,
        info=HKDF_INFO,
        backend=default_backend(),
    ).derive(shared)

    # AES-256-GCM encrypt
    iv = os.urandom(12)
    aesgcm = AESGCM(derived)
    plaintext = json.dumps(envelope["data"], ensure_ascii=False).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)

    # Replace data with EncryptedPayload
    envelope["data"] = {
        "ephemeralKey": base64.b64encode(eph_pub_bytes).decode(),
        "iv": base64.b64encode(iv).decode(),
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    envelope["encrypted"] = True
    return envelope


def decrypt_payload(encrypted_data: dict, private_key) -> dict:
    """Decrypt an EncryptedPayload back to the original data dict.

    Args:
        encrypted_data: Dict with keys 'ephemeralKey', 'iv', 'ciphertext'.
        private_key: The local ECDH private key (ec.EllipticCurvePrivateKey).

    Returns:
        The decrypted data dict.
    """
    eph_key_bytes = base64.b64decode(encrypted_data["ephemeralKey"])
    iv = base64.b64decode(encrypted_data["iv"])
    ciphertext = base64.b64decode(encrypted_data["ciphertext"])

    eph_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), eph_key_bytes
    )
    shared = private_key.exchange(ec.ECDH(), eph_pub)

    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=HKDF_SALT,
        info=HKDF_INFO,
        backend=default_backend(),
    ).derive(shared)

    aesgcm = AESGCM(derived)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def get_phone_public_key(config) -> Optional[str]:
    """Get the phone's E2EE public key from config cache or relay endpoint.

    Tries the cached phone_public_key in config first. If not found,
    fetches from the relay's /pubkey endpoint and caches the result.

    Args:
        config: ConfigDict with relay_url, jwt, and optionally phone_public_key.

    Returns:
        Base64-encoded public key string, or None if unavailable.
    """
    cached = config.get("phone_public_key")
    if cached and isinstance(cached, str) and cached.strip():
        return cached.strip()

    relay_url = config.get("relay_url", "")
    jwt = config.get("jwt", "")
    if not relay_url or not jwt:
        return None

    try:
        import httpx
        with httpx.Client(timeout=5) as client:
            resp = client.get(
                f"{relay_url}/pubkey",
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                pub_key = data.get("publicKey")
                if pub_key and isinstance(pub_key, str):
                    # Cache to config
                    from config_manager import ConfigManager
                    cfg = ConfigManager.load()
                    cfg["phone_public_key"] = pub_key
                    ConfigManager.save(cfg)
                    return pub_key
    except Exception:
        pass

    return None
