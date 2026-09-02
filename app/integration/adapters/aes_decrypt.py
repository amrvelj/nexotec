"""AES decryption for auto-i-dat's encrypted SOAP responses (WP-6 PR-3).

**Provisional**: auto-i-dat's exact AES mode/padding/IV convention is not
confirmed against real vendor documentation or a real sample payload in
this session. AES-CBC with PKCS7 padding and a 16-byte IV prepended to
the ciphertext is assumed here as the widely-used, standard shape — flag
this against the real `SS 03 ... Schnittstellenbeschrieb` spec (in Drive)
before this adapter is ever pointed at a live account. The one test
exercising this function is built against a self-constructed vector
(`encrypt_aes_cbc` below, encrypt then decrypt), not a real vendor sample.

Isolated from the SOAP transport on purpose — `decrypt_aes_cbc` has no
dependency on `zeep` or any network call, so it is unit-testable on its
own with a known key/IV/plaintext, independent of whether a real
auto-i-dat account is ever reachable from this environment.
"""

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_IV_LENGTH = 16
_VALID_KEY_LENGTHS = (16, 24, 32)  # AES-128 / AES-192 / AES-256


def decrypt_aes_cbc(ciphertext: bytes, *, key: bytes) -> bytes:
    """`ciphertext` is the IV (first 16 bytes) followed by the encrypted
    payload — already base64-decoded by the caller (the SOAP response
    itself carries base64 text). `key` is the per-account AES key,
    resolved via `services/secrets_backend.py` in memory, for the
    duration of one call only.
    """

    if len(key) not in _VALID_KEY_LENGTHS:
        raise ValueError(f"AES key must be 16, 24 or 32 bytes; got {len(key)}.")
    if len(ciphertext) <= _IV_LENGTH:
        raise ValueError("Ciphertext is too short to contain an IV plus any payload.")

    iv, encrypted = ciphertext[:_IV_LENGTH], ciphertext[_IV_LENGTH:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def encrypt_aes_cbc(plaintext: bytes, *, key: bytes, iv: bytes) -> bytes:
    """Test-only — constructs a self-consistent ciphertext so
    `decrypt_aes_cbc` has something real to decrypt against without a
    live vendor sample. Never called outside tests.
    """

    if len(iv) != _IV_LENGTH:
        raise ValueError(f"IV must be {_IV_LENGTH} bytes; got {len(iv)}.")
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + encryptor.update(padded) + encryptor.finalize()
