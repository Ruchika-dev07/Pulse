import re
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

def load_pub(path="chip1_public_key.txt"):
    txt = open(path).read()
    toks = re.findall(r"0x([0-9a-fA-F]{2})", txt)
    if toks:
        raw = bytes(int(t, 16) for t in toks)
    else:
        raw = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", txt))
    if len(raw) == 65 and raw[0] == 4:
        raw = raw[1:]
    if len(raw) != 64:
        raise ValueError("public key should be 64 bytes, got %d" % len(raw))
    x = int.from_bytes(raw[:32], "big")
    y = int.from_bytes(raw[32:], "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()

def check(pub, message, sig_hex):
    sig = bytes.fromhex(sig_hex)
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    try:
        pub.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
        return True
    except Exception:
        return False

if __name__ == "__main__":
    pub = load_pub()
    line1 = b'{"type":"payment_request","id":"3f2a9c1e-7b4d-4e8a-9c2f-1a5b6d7e8f90","provider":"Amazon.com","requester":"Ruchika","amount":500.0}'
    sig1 = "BC86F043B102FFA140A42EBC04E49B39004B3767DF64BA7B1D29613F68951036324D5B6C9CDFDFEF2D9BA8DFB8ACCF174F322220F1AA6365823E4D49826C2BAA"
    line2 = b'{"type":"session_request","id":"8d1e4b2c-5a6f-4c3d-b7e9-0f1a2b3c4d5e"}'
    sig2 = "3894013906F933C46D477D63DA5BB95416EB69CD9B325D4F0DA3DF2BA9C7CAA875A58DF9DC7431D685E0BD19F0EFB3517872C3A6E43A8BC4E14950E8EB7FA6E8"
    print("payment_request:", check(pub, line1 + b"|authorized", sig1))
    print("session_request:", check(pub, line2 + b"|authorized", sig2))