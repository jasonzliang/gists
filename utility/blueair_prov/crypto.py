"""Security1 primitives, ported from service.rs step_session0/step_session1.

Rust:
    let secret_key = EphemeralSecret::random();        // x25519-dalek
    let shared_secret = secret_key.diffie_hellman(&device_pubkey);
    type Aes256Ctr = ctr::Ctr32BE<aes::Aes256>;
    self.cipher = Some(Aes256Ctr::new(shared_secret.as_bytes().into(), device_random.into()));
    ... self.cipher.apply_keystream(buf)  // ONE cipher, used for every encrypt AND decrypt

Key facts reproduced here:
  * Key = the raw 32-byte X25519 shared secret. The Rust code never mixes a
    proof-of-possession (PoP) in, i.e. the device runs protocomm Security1 with
    pop == NULL. `pop=` below is an OPTIONAL extension implementing the
    standard ESP-IDF rule key = shared_secret XOR SHA256(pop); it is off by
    default and is NOT exercised by the Rust reference.
  * IV = device_random (16 bytes from SessionResp0).
  * A single continuous keystream is shared by all encrypt and decrypt calls
    in wire order (CTR is symmetric, so "decrypt" is the same XOR).
  * Counter width: Rust uses Ctr32BE (32-bit big-endian counter in the last
    4 IV bytes). The device (ESP-IDF/mbedtls `mbedtls_aes_crypt_ctr`) increments
    the whole 128-bit block big-endian. The two agree unless the low 32 bits of
    the IV wrap during the session (probability ~ session_bytes/16 / 2^32).
    We implement CTR by hand on top of AES-ECB so both widths are available;
    default 128 (= device), `counter_bits=32` reproduces Rust bit-for-bit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

X25519_KEY_LEN = 32
AES_BLOCK = 16


class KeystreamCipher:
    """AES-256-CTR as a stateful keystream (mirrors ctr::Ctr32BE<Aes256>::apply_keystream)."""

    def __init__(self, key: bytes, iv: bytes, counter_bits: int = 128):
        if len(key) != 32:
            raise ValueError(f"AES-256 key must be 32 bytes, got {len(key)}")
        if len(iv) != AES_BLOCK:
            raise ValueError(f"CTR IV must be 16 bytes, got {len(iv)}")
        if counter_bits not in (32, 128):
            raise ValueError("counter_bits must be 32 or 128")
        self._ecb = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 (CTR built on ECB)
        self._iv = bytes(iv)
        self._counter_bits = counter_bits
        self._block_index = 0  # number of counter blocks consumed
        self._pending = b""    # unused keystream bytes from the current block
        self.bytes_processed = 0

    def _counter_block(self, n: int) -> bytes:
        if self._counter_bits == 128:
            v = (int.from_bytes(self._iv, "big") + n) % (1 << 128)
            return v.to_bytes(16, "big")
        # Ctr32BE: only the last 4 bytes increment, wrapping mod 2^32.
        prefix, ctr = self._iv[:12], int.from_bytes(self._iv[12:], "big")
        return prefix + ((ctr + n) % (1 << 32)).to_bytes(4, "big")

    def apply_keystream(self, data: bytes) -> bytes:
        """XOR `data` with the next len(data) keystream bytes (encrypt == decrypt)."""
        data = bytes(data)
        need = len(data) - len(self._pending)
        chunks = [self._pending]
        while need > 0:
            block = self._ecb.update(self._counter_block(self._block_index))
            self._block_index += 1
            chunks.append(block)
            need -= AES_BLOCK
        ks = b"".join(chunks)
        out = bytes(a ^ b for a, b in zip(data, ks, strict=False))
        self._pending = ks[len(data):]
        self.bytes_processed += len(data)
        return out


class NullCipher:
    """Security0: plaintext. Same interface as KeystreamCipher."""

    bytes_processed = 0

    def apply_keystream(self, data: bytes) -> bytes:
        return bytes(data)


def pop_mix(shared_secret: bytes, pop: str | bytes | None) -> bytes:
    """ESP-IDF security1: key = shared_secret XOR SHA256(pop) when a PoP is set.
    Returns shared_secret unchanged for pop=None/"" (the Rust behaviour)."""
    if not pop:
        return bytes(shared_secret)
    if isinstance(pop, str):
        pop = pop.encode("utf-8")
    digest = hashlib.sha256(pop).digest()
    return bytes(a ^ b for a, b in zip(shared_secret, digest, strict=False))


@dataclass
class ClientKeys:
    private: X25519PrivateKey
    public_bytes: bytes

    @classmethod
    def generate(cls) -> "ClientKeys":
        priv = X25519PrivateKey.generate()
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cls(priv, pub)

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "ClientKeys":
        priv = X25519PrivateKey.from_private_bytes(raw)
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cls(priv, pub)

    def shared_secret(self, device_pubkey: bytes) -> bytes:
        if len(device_pubkey) != X25519_KEY_LEN:
            raise ValueError(f"device_pubkey must be 32 bytes, got {len(device_pubkey)}")
        return self.private.exchange(X25519PublicKey.from_public_bytes(device_pubkey))


def derive_session_cipher(
    keys: ClientKeys,
    device_pubkey: bytes,
    device_random: bytes,
    pop: str | bytes | None = None,
    counter_bits: int = 128,
) -> KeystreamCipher:
    """step_session0 tail: Aes256Ctr::new(shared_secret, device_random)."""
    key = pop_mix(keys.shared_secret(device_pubkey), pop)
    return KeystreamCipher(key, device_random, counter_bits=counter_bits)
