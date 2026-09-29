import hashlib
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from blueair_prov.crypto import ClientKeys, KeystreamCipher, derive_session_cipher, pop_mix

# NIST SP 800-38A F.5.5 CTR-AES256.Encrypt
NIST_KEY = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
NIST_IV = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
NIST_PT = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a" "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef" "f69f2445df4f9b17ad2b417be66c3710"
)
NIST_CT = bytes.fromhex(
    "601ec313775789a5b7a7f504bbf3d228" "f443e3ca4d62b59aca84e990cacaf5c5"
    "2b0930daa23de94ce87017ba2d84988d" "dfc9c58db67aada613c2dd08457941a6"
)

# RFC 7748 section 6.1
ALICE_PRIV = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
ALICE_PUB = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
BOB_PRIV = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
BOB_PUB = bytes.fromhex("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
SHARED = bytes.fromhex("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")


class TestAesCtr(unittest.TestCase):
    def test_nist_vector_128bit_counter(self):
        for bits in (128, 32):  # IV low 32 bits fcfdfeff + 3 blocks does not wrap
            c = KeystreamCipher(NIST_KEY, NIST_IV, counter_bits=bits)
            self.assertEqual(c.apply_keystream(NIST_PT), NIST_CT, bits)

    def test_streaming_matches_one_shot_and_library(self):
        c1 = KeystreamCipher(NIST_KEY, NIST_IV)
        parts = [NIST_PT[:5], NIST_PT[5:17], NIST_PT[17:18], NIST_PT[18:40], NIST_PT[40:]]
        streamed = b"".join(c1.apply_keystream(p) for p in parts)
        self.assertEqual(streamed, NIST_CT)
        lib = Cipher(algorithms.AES(NIST_KEY), modes.CTR(NIST_IV)).encryptor()
        self.assertEqual(b"".join(lib.update(p) for p in parts), NIST_CT)

    def test_encrypt_then_decrypt_shares_keystream(self):
        """Rust uses ONE Aes256Ctr for both directions; simulate client+device."""
        key, iv = bytes(range(32)), bytes(range(16))
        client = KeystreamCipher(key, iv)
        device = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()  # independent impl
        for msg, reply in [(b"hello", b"world!!"), (b"x" * 33, b"y" * 3), (b"", b"z")]:
            ct = client.apply_keystream(msg)
            self.assertEqual(device.update(ct), msg)
            rct = device.update(reply)
            self.assertEqual(client.apply_keystream(rct), reply)

    def test_ctr32_vs_ctr128_diverge_only_on_wrap(self):
        key = bytes(32)
        iv = bytes.fromhex("00112233445566778899aabb" "ffffffff")  # low word about to wrap
        a = KeystreamCipher(key, iv, counter_bits=128).apply_keystream(bytes(48))
        b = KeystreamCipher(key, iv, counter_bits=32).apply_keystream(bytes(48))
        self.assertEqual(a[:16], b[:16])   # first block identical
        self.assertNotEqual(a[16:32], b[16:32])  # after wrap, Ctr32BE stays in the same 96-bit prefix
        iv2 = bytes.fromhex("00112233445566778899aabb" "00000001")
        self.assertEqual(
            KeystreamCipher(key, iv2, 128).apply_keystream(bytes(4096)),
            KeystreamCipher(key, iv2, 32).apply_keystream(bytes(4096)),
        )

    def test_bad_lengths(self):
        with self.assertRaises(ValueError):
            KeystreamCipher(bytes(16), bytes(16))
        with self.assertRaises(ValueError):
            KeystreamCipher(bytes(32), bytes(12))


class TestX25519(unittest.TestCase):
    def test_rfc7748_vector(self):
        alice = ClientKeys.from_private_bytes(ALICE_PRIV)
        self.assertEqual(alice.public_bytes, ALICE_PUB)
        self.assertEqual(alice.shared_secret(BOB_PUB), SHARED)
        bob = ClientKeys.from_private_bytes(BOB_PRIV)
        self.assertEqual(bob.public_bytes, BOB_PUB)
        self.assertEqual(bob.shared_secret(ALICE_PUB), SHARED)

    def test_generate_distinct(self):
        a, b = ClientKeys.generate(), ClientKeys.generate()
        self.assertNotEqual(a.public_bytes, b.public_bytes)
        self.assertEqual(len(a.public_bytes), 32)


class TestPop(unittest.TestCase):
    def test_no_pop_is_identity(self):
        self.assertEqual(pop_mix(SHARED, None), SHARED)
        self.assertEqual(pop_mix(SHARED, ""), SHARED)

    def test_pop_xor_sha256(self):
        d = hashlib.sha256(b"abcd1234").digest()
        self.assertEqual(pop_mix(SHARED, "abcd1234"), bytes(a ^ b for a, b in zip(SHARED, d)))

    def test_derive_session_cipher_no_pop_uses_raw_shared_secret(self):
        alice = ClientKeys.from_private_bytes(ALICE_PRIV)
        iv = bytes(range(16))
        c = derive_session_cipher(alice, BOB_PUB, iv)
        ref = Cipher(algorithms.AES(SHARED), modes.CTR(iv)).encryptor()
        self.assertEqual(c.apply_keystream(b"A" * 40), ref.update(b"A" * 40))


if __name__ == "__main__":
    unittest.main()
