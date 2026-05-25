"""
encryption.py – Ma hoa/giai ma tin nhan P2P
--------------------------------------------
Su dung AES-256 qua thu vien `cryptography` (Fernet).
Neu thu vien chua cai dat, tu dong dung XOR cipher de minh hoa.

Cai dat thu vien:
    pip install cryptography
"""
import base64
import hashlib
import os

try:
    # pyrefly: ignore [missing-import]
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class MessageEncryptor:
    """
    Ma hoa va giai ma tin nhan bang shared key.

    Khi ca hai peer dong y dung cung mot key (vi du: "secret123"),
    tin nhan truyen qua mang se duoc ma hoa AES-256 (hoac XOR fallback).

    Vi du:
        enc = MessageEncryptor("secret123")
        cipher = enc.encrypt("Xin chao!")
        plain  = enc.decrypt(cipher)   # → "Xin chao!"
    """

    def __init__(self, shared_key: str):
        """
        Args:
            shared_key: Chuoi mat khau chung giua 2 peer.
                        Phai giong nhau o ca 2 dau moi giai ma duoc.
        """
        if not shared_key:
            raise ValueError("shared_key khong duoc de trong.")

        self.shared_key = shared_key
        self._mode = "AES-256 (Fernet)" if CRYPTO_AVAILABLE else "XOR (fallback)"

        if CRYPTO_AVAILABLE:
            # Derive 32-byte key tu shared_key bang SHA-256
            raw_key = hashlib.sha256(shared_key.encode("utf-8")).digest()
            # Fernet yeu cau URL-safe base64 cua 32 bytes
            fernet_key = base64.urlsafe_b64encode(raw_key)
            self._fernet = Fernet(fernet_key)

    @property
    def mode(self) -> str:
        """Tra ve ten che do ma hoa dang dung."""
        return self._mode

    # ── Ma hoa ──────────────────────────────────────────────

    def encrypt(self, plaintext: str) -> str:
        """
        Ma hoa plaintext, tra ve chuoi base64 an toan.
        Args:
            plaintext: Tin nhan goc can ma hoa.
        Returns:
            Chuoi ciphertext da ma hoa (co the truyen qua mang).
        """
        if CRYPTO_AVAILABLE:
            token = self._fernet.encrypt(plaintext.encode("utf-8"))
            return base64.urlsafe_b64encode(token).decode("ascii")
        else:
            return self._xor_encrypt(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        """
        Giai ma ciphertext, tra ve plaintext goc.
        Args:
            ciphertext: Chuoi da ma hoa nhan tu mang.
        Returns:
            Plaintext goc, hoac raise ValueError neu key sai.
        """
        if CRYPTO_AVAILABLE:
            try:
                raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
                return self._fernet.decrypt(raw).decode("utf-8")
            except (InvalidToken, Exception) as e:
                raise ValueError(f"Giai ma that bai (key sai hoac du lieu bi hong): {e}")
        else:
            return self._xor_decrypt(ciphertext)

    # ── XOR Cipher Fallback ──────────────────────────────────

    def _xor_encrypt(self, text: str) -> str:
        """XOR cipher don gian (khi cryptography chua cai)."""
        key = self.shared_key
        encrypted = bytes(
            ord(c) ^ ord(key[i % len(key)])
            for i, c in enumerate(text)
        )
        return base64.b64encode(encrypted).decode("ascii")

    def _xor_decrypt(self, encoded: str) -> str:
        """Giai ma XOR cipher."""
        key = self.shared_key
        encrypted = base64.b64decode(encoded.encode("ascii"))
        return "".join(
            chr(b ^ ord(key[i % len(key)]))
            for i, b in enumerate(encrypted)
        )

    # ── Tao key ngau nhien ───────────────────────────────────

    @staticmethod
    def generate_key() -> str:
        """
        Sinh ra mot key ngau nhien 32 ky tu an toan de dung lam shared_key.
        Returns:
            Chuoi hex 32 ky tu.
        """
        return os.urandom(16).hex()
