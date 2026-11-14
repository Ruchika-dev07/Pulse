from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes

PUB = bytes.fromhex("11D0A534BEC5B239F3825B6053365CAEE33A62656A2B2786D1F90D7C0D4BE009"
                    "891EABF60AE63AD5DF5DAE9CBC702D6F52FC91C9A471263B0E08777DB71A7228")

def verify(digest32: bytes, sig64: bytes) -> bool:
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + PUB)
    r = int.from_bytes(sig64[:32], "big")
    s = int.from_bytes(sig64[32:], "big")
    try:
        key.verify(utils.encode_dss_signature(r, s), digest32,
                   ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        return True
    except Exception:
        return False

if __name__ == "__main__":
    msg = bytes(range(32))
    sig = bytes.fromhex(
        "33FB8484DED99F5347A6C5FAA2BC61D36A82AD3CB7AB68D304A542B93B9894C2"
        "A4160D3C3D0D979850A2F1EE70D7849EF4C8284CEF51B199018B7A88CD8BF230")
    print(verify(msg, sig))
    print(verify(bytes([1]) + msg[1:], sig))