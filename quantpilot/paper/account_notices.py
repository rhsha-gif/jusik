"""Fail-closed decoding for KIS paper account notices.

This module intentionally turns an encrypted notice into only a reconciliation
hint.  It never exposes account, order, quantity, price, customer, or raw payload
fields, and it has no authority to mutate execution or cash state.
"""

from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re

from quantpilot.paper.calendar import KST
from quantpilot.paper.config import aware
from quantpilot.paper.data import symbol_code


NOTICE_TR = "H0STCNI9"
_NOTICE_FIELDS = 26
_MAX_FRAME = 65_536


@dataclass(frozen=True, repr=False)
class NoticeCipher:
    """In-memory AES-256-CBC material accepted only from a subscription ACK."""

    _key: bytes
    _iv: bytes

    @classmethod
    def from_subscription_ack(cls, key: object, iv: object) -> "NoticeCipher":
        if not isinstance(key, str) or not isinstance(iv, str):
            raise ValueError("notice_ack_crypto_missing")
        try:
            key_bytes, iv_bytes = key.encode("utf-8"), iv.encode("utf-8")
        except UnicodeError:
            raise ValueError("notice_ack_crypto_invalid") from None
        if len(key_bytes) != 32 or len(iv_bytes) != 16:
            raise ValueError("notice_ack_crypto_invalid")
        return cls(key_bytes, iv_bytes)

    def decrypt(self, encoded: str) -> str:
        if not isinstance(encoded, str) or len(encoded) > _MAX_FRAME:
            raise ValueError("notice_ciphertext_invalid")
        try:
            ciphertext = b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ValueError("notice_ciphertext_invalid") from None
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError("notice_ciphertext_invalid")
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        try:
            decoder = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
            padded = decoder.update(ciphertext) + decoder.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            raise ValueError("notice_padding_invalid") from None
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("notice_plaintext_invalid") from None



def parse_notice_frame(
    raw: object,
    *,
    cipher: NoticeCipher | None,
    received_at: datetime,
    registered: bool,
) -> dict[str, str]:
    """Return a redacted REST-reconciliation hint for one registered paper notice."""

    received_at = aware(received_at)
    if not registered or cipher is None:
        raise ValueError("notice_subscription_unregistered")
    if not isinstance(raw, str) or len(raw) > _MAX_FRAME:
        raise ValueError("notice_frame_invalid")
    parts = raw.split("|", 3)
    if len(parts) != 4 or parts[:3] != ["1", NOTICE_TR, "001"]:
        raise ValueError("notice_frame_mismatch")
    fields = cipher.decrypt(parts[3]).split("^")
    if len(fields) != _NOTICE_FIELDS:
        raise ValueError("notice_shape_invalid")
    try:
        symbol = symbol_code(fields[8])
        if fields[13] not in {"1", "2"} or fields[12] not in {"0", "1"}:
            raise ValueError
        if not re.fullmatch(r"[0-9]{6}", fields[11]):
            raise ValueError
        occurred = datetime.strptime(
            received_at.astimezone(KST).date().isoformat() + " " + fields[11],
            "%Y-%m-%d %H%M%S",
        ).replace(tzinfo=KST)
    except (ValueError, TypeError):
        raise ValueError("notice_payload_invalid") from None
    age = (received_at - occurred).total_seconds()
    if age < -1 or age >= 30:
        raise ValueError("notice_timestamp_invalid")
    normalized = min(occurred, received_at)
    return {
        "kind": "reconciliation_hint",
        "reason": "paper_execution_notice",
        "notice_kind": "execution" if fields[13] == "2" else "order_status",
        "symbol": symbol,
        "event_id": sha256(raw.encode("utf-8")).hexdigest(),
        "occurred_at": occurred.astimezone(timezone.utc).isoformat(),
        "observed_at": normalized.astimezone(timezone.utc).isoformat(),
        "received_at": received_at.astimezone(timezone.utc).isoformat(),
    }
