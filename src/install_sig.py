#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-install Ed25519 signatures over bucket hash.

Stdlib-friendly: prefers `cryptography`, then OpenSSL CLI, else unsigned.
Not DRM — stops bulk fabrication without the install private key.
"""
from __future__ import annotations

import base64, hashlib, os, shutil, subprocess, tempfile
from pathlib import Path

ALG = "ed25519"

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")

def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=False)

def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey, Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives import serialization
        return Ed25519PrivateKey, Ed25519PublicKey, serialization
    except Exception:
        return None, None, None

def _openssl() -> str | None:
    return shutil.which("openssl")

def key_paths(config_home: Path):
    config_home = Path(config_home)
    config_home.mkdir(parents=True, exist_ok=True)
    return config_home / "install_ed25519.pem", config_home / "install_ed25519.pub"

def install_id_from_pubkey(pub_raw: bytes) -> str:
    return "inst_" + hashlib.sha256(pub_raw).hexdigest()[:16]

def generate_keypair_crypto(priv_path: Path, pub_path: Path):
    Ed25519PrivateKey, _, serialization = _crypto()
    if Ed25519PrivateKey is None:
        return None
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    try:
        priv_path.chmod(0o600)
    except Exception:
        pass
    return priv, priv.public_key()

def generate_keypair_openssl(priv_path: Path, pub_path: Path):
    openssl = _openssl()
    if not openssl:
        return False
    subprocess.run(
        [openssl, "genpkey", "-algorithm", "Ed25519", "-out", str(priv_path)],
        check=True, capture_output=True,
    )
    subprocess.run(
        [openssl, "pkey", "-in", str(priv_path), "-pubout", "-out", str(pub_path)],
        check=True, capture_output=True,
    )
    try:
        priv_path.chmod(0o600)
    except Exception:
        pass
    return True

def load_or_create_keypair(config_home: Path):
    """Return (priv_path, pub_path, pub_raw_32, install_id) or None if unavailable."""
    priv_path, pub_path = key_paths(config_home)
    if not priv_path.exists() or not pub_path.exists():
        if generate_keypair_crypto(priv_path, pub_path) is None:
            if not generate_keypair_openssl(priv_path, pub_path):
                return None
    pub_raw = extract_raw_pubkey(pub_path.read_bytes())
    if pub_raw is None or len(pub_raw) != 32:
        return None
    return priv_path, pub_path, pub_raw, install_id_from_pubkey(pub_raw)

def extract_raw_pubkey(pub_pem: bytes) -> bytes | None:
    Ed25519PrivateKey, Ed25519PublicKey, serialization = _crypto()
    if Ed25519PublicKey is not None:
        try:
            pub = serialization.load_pem_public_key(pub_pem)
            return pub.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        except Exception:
            pass
    openssl = _openssl()
    if not openssl:
        return None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(pub_pem)
            tmp_path = tmp.name
        try:
            der = subprocess.check_output(
                [openssl, "pkey", "-pubin", "-in", tmp_path, "-outform", "DER"],
                stderr=subprocess.DEVNULL,
            )
        finally:
            os.unlink(tmp_path)
        # SubjectPublicKeyInfo for Ed25519: last 32 bytes are the raw key
        if len(der) >= 32:
            return der[-32:]
    except Exception:
        return None
    return None

def sign_hash(hash_hex: str, priv_path: Path) -> str | None:
    """Return base64 signature over UTF-8 hash hex, or None."""
    msg = hash_hex.encode("ascii")
    Ed25519PrivateKey, _, serialization = _crypto()
    if Ed25519PrivateKey is not None:
        try:
            priv = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
            return _b64(priv.sign(msg))
        except Exception:
            pass
    openssl = _openssl()
    if not openssl:
        return None
    try:
        sig = subprocess.check_output(
            [openssl, "pkeyutl", "-sign", "-inkey", str(priv_path), "-rawin"],
            input=msg, stderr=subprocess.DEVNULL,
        )
        return _b64(sig)
    except Exception:
        return None

def verify_signature(hash_hex: str, signature_b64: str, pub_raw: bytes) -> bool:
    if not hash_hex or not signature_b64 or not pub_raw or len(pub_raw) != 32:
        return False
    msg = hash_hex.encode("ascii")
    try:
        sig = _unb64(signature_b64)
    except Exception:
        return False
    if len(sig) != 64:
        return False
    _, Ed25519PublicKey, serialization = _crypto()
    if Ed25519PublicKey is not None:
        try:
            pub = Ed25519PublicKey.from_public_bytes(pub_raw)
            pub.verify(sig, msg)
            return True
        except Exception:
            return False
    openssl = _openssl()
    if not openssl:
        return False
    try:
        with tempfile.TemporaryDirectory() as td:
            pub_path = Path(td) / "pub.pem"
            # Rebuild SPKI PEM from raw key via openssl
            der_prefix = bytes.fromhex("302a300506032b6570032100")
            der = der_prefix + pub_raw
            spki = Path(td) / "pub.der"
            spki.write_bytes(der)
            subprocess.check_call(
                [openssl, "pkey", "-pubin", "-inform", "DER", "-in", str(spki),
                 "-out", str(pub_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            sig_path = Path(td) / "sig.bin"
            sig_path.write_bytes(sig)
            msg_path = Path(td) / "msg.txt"
            msg_path.write_bytes(msg)
            subprocess.check_call(
                [openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(pub_path),
                 "-sigfile", str(sig_path), "-rawin", "-in", str(msg_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
    except Exception:
        return False

def sign_bucket(bucket: dict, config_home: Path) -> dict:
    """Attach install_id, install_pubkey, signature. Mutates and returns bucket."""
    loaded = load_or_create_keypair(config_home)
    if not loaded:
        bucket.setdefault("signature", None)
        return bucket
    priv_path, _pub_path, pub_raw, iid = loaded
    bucket["install_id"] = iid
    bucket["install_pubkey"] = _b64(pub_raw)
    # hash must already be set
    h = bucket.get("hash")
    if not h:
        return bucket
    sig = sign_hash(h, priv_path)
    bucket["signature"] = sig
    bucket["sig_alg"] = ALG
    return bucket
