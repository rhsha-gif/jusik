from datetime import datetime, timedelta

import pytest

from quantpilot.paper.account_notices import NoticeCipher, parse_notice_frame
from quantpilot.paper.calendar import KST


KEY = "0" * 32  # Synthetic AES fixture, never a broker credential.
IV = "abcdef0123456789"
CIPHERTEXT = (
    'S7v3/wg4AdVNmRAH5JJmWSMtDnUqAr7a0AN1LEbGM6Uys5DEqONEo2a/eNSjsSsAXEsmjujEqpwtgKPTu+/ziuTjjfAQun89BVmMCo7ra119ooJr6/CXb7abuzeBiQQx/SNblRSNuZjibsZrbnylCq7kWPVMissNmg4Dh8/4qoTg7G91CTenPtqoHzKjOwAo'
)
FRAME = f"1|H0STCNI9|001|{CIPHERTEXT}"
NOW = datetime(2026, 9, 14, 10, 0, 1, tzinfo=KST)


def test_aes256_cbc_notice_yields_only_redacted_reconciliation_hint() -> None:
    cipher = NoticeCipher.from_subscription_ack(KEY, IV)

    hint = parse_notice_frame(
        FRAME, cipher=cipher, received_at=NOW, registered=True
    )

    assert hint["kind"] == "reconciliation_hint"
    assert hint["notice_kind"] == "execution"
    assert hint["symbol"] == "005930"
    assert set(hint) == {
        "kind",
        "reason",
        "notice_kind",
        "symbol",
        "event_id",
        "occurred_at",
        "observed_at",
        "received_at",
    }
    rendered = repr(hint)
    assert "fixture-customer" not in rendered
    assert "12345678" not in rendered
    assert "ORDER-SECRET" not in rendered
    assert "70000" not in rendered


def test_notice_normalizes_one_second_future_and_rejects_more_future_or_old() -> None:
    cipher = NoticeCipher.from_subscription_ack(KEY, IV)

    hint = parse_notice_frame(
        FRAME,
        cipher=cipher,
        received_at=NOW - timedelta(seconds=1),
        registered=True,
    )
    assert hint["observed_at"] == hint["received_at"]
    assert hint["occurred_at"] != hint["received_at"]

    with pytest.raises(ValueError, match="timestamp"):
        parse_notice_frame(
            FRAME,
            cipher=cipher,
            received_at=NOW - timedelta(seconds=2),
            registered=True,
        )
    with pytest.raises(ValueError, match="timestamp"):
        parse_notice_frame(
            FRAME,
            cipher=cipher,
            received_at=NOW + timedelta(seconds=30),
            registered=True,
        )


@pytest.mark.parametrize(
    ("raw", "registered", "reason"),
    [
        (FRAME, False, "unregistered"),
        (f"0|H0STCNI9|001|{CIPHERTEXT}", True, "mismatch"),
        (f"1|H0STASP0|001|{CIPHERTEXT}", True, "mismatch"),
        ("1|H0STCNI9|001|not-base64!", True, "ciphertext"),
        (f"1|H0STCNI9|002|{CIPHERTEXT}", True, "mismatch"),
    ],
)
def test_malformed_unregistered_and_public_encryption_mismatches_fail_closed(
    raw: str, registered: bool, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        parse_notice_frame(
            raw,
            cipher=NoticeCipher.from_subscription_ack(KEY, IV),
            received_at=NOW,
            registered=registered,
        )


def test_ack_material_and_pkcs7_padding_are_strict() -> None:
    with pytest.raises(ValueError, match="crypto"):
        NoticeCipher.from_subscription_ack("short", IV)
    with pytest.raises(ValueError, match="crypto"):
        NoticeCipher.from_subscription_ack(KEY, "short")

    damaged = CIPHERTEXT[:-2] + "AA"
    with pytest.raises(ValueError, match="padding|plaintext"):
        NoticeCipher.from_subscription_ack(KEY, IV).decrypt(damaged)
