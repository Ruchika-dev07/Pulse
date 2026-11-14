"""
V2: verify that an approval really came from the wearable's ATECC608 chip.

Adapted from ESP32-Biometric/verify.py (Karimov). The chip holds a private key that can
never be read out; the laptop only knows the public key below.

What the chip signs
  When all three checks pass, the firmware signs
      SHA-256( <the exact request line the laptop sent, without the newline> + "|authorized" )
  and puts the 64-byte signature (r||s, hex) in the result line as "sig".

Why this is safe
  - The request line contains the payment amount and payee, so a signature for $50 to Amazon
    can't be reused for $5,000 to someone else.
  - Every request has a fresh random "id" (the nonce). The backend only accepts a result for a
    request that is still pending, so a recorded reply can't be replayed later.
  - The backend computes the hash itself from what it sent. It never trusts a hash from the device.
"""

import hashlib
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

# Chip 1, serial 01238D68A84D0601EE (ESP32-Biometric/chip1_public_key.txt)
CHIP1_PUBLIC_KEY = (
    "11D0A534BEC5B239F3825B6053365CAEE33A62656A2B2786D1F90D7C0D4BE009"
    "891EABF60AE63AD5DF5DAE9CBC702D6F52FC91C9A471263B0E08777DB71A7228"
)

APPROVAL_SUFFIX = "|authorized"


def _load_key():
    hex_key = (os.getenv("DEVICE_PUBKEY") or CHIP1_PUBLIC_KEY).strip().replace(" ", "")
    raw = bytes.fromhex(hex_key)
    if len(raw) != 64:
        raise ValueError("DEVICE_PUBKEY must be 64 bytes (128 hex characters): X then Y")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + raw)


_KEY = _load_key()


def verify(digest32: bytes, sig64: bytes) -> bool:
    """Same contract as Karimov's verify.py: True only if the chip signed exactly this digest."""
    if len(digest32) != 32 or len(sig64) != 64:
        return False
    r = int.from_bytes(sig64[:32], "big")
    s = int.from_bytes(sig64[32:], "big")
    try:
        _KEY.verify(utils.encode_dss_signature(r, s), digest32, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return True
    except Exception:
        return False


def approval_digest(request_line: str) -> bytes:
    return hashlib.sha256((request_line + APPROVAL_SUFFIX).encode("utf-8")).digest()


def verify_approval(request_line: str, sig_hex: str) -> bool:
    try:
        sig = bytes.fromhex(sig_hex.strip())
    except (ValueError, AttributeError):
        return False
    return verify(approval_digest(request_line), sig)


if __name__ == "__main__":
    # Karimov's test vector: chip 1 signed bytes 0..31
    sig = bytes.fromhex(
        "33FB8484DED99F5347A6C5FAA2BC61D36A82AD3CB7AB68D304A542B93B9894C2"
        "A4160D3C3D0D979850A2F1EE70D7849EF4C8284CEF51B199018B7A88CD8BF230"
    )
    print("chip 1 test vector verifies:", verify(bytes(range(32)), sig))
    print("tampered message rejected:  ", not verify(bytes([1]) + bytes(range(32))[1:], sig))
