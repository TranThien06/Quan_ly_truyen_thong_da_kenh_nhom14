import base64
import json
from pathlib import Path
import pytest
from app.security import encrypt, decrypt, hash_password, verify_password, hash_token, token
from app.common import utc, valid_id, public
from app.jobs.reports import safe_cell

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "express_crypto.json").read_text())


def test_reads_bcrypt_hash_from_express():
    assert verify_password(FIXTURE["password"], FIXTURE["bcrypt_hash"])
    assert not verify_password("wrong", FIXTURE["bcrypt_hash"])
    first, second = hash_password("Password123!"), hash_password("Password123!")
    assert first != second
    assert verify_password("Password123!", first)


def test_reads_node_aes_gcm_bytes_and_rejects_tampering(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", FIXTURE["test_key"])
    assert decrypt(base64.b64decode(FIXTURE["ciphertext"])) == FIXTURE["plaintext"]
    encrypted = encrypt("tiếng Việt")
    assert decrypt(encrypted) == "tiếng Việt"
    damaged = bytearray(encrypted)
    damaged[15] ^= 1
    with pytest.raises(Exception):
        decrypt(damaged)
    assert encrypt("same") != encrypt("same")


def test_tokens_timezones_and_bigint():
    assert token() != token()
    assert len(token()) == 43
    assert len(hash_token("test")) == 64
    assert utc("2027-01-01T09:00:00+07:00").isoformat() == "2027-01-01T02:00:00"
    with pytest.raises(Exception):
        utc("2027-01-01 09:00:00")
    assert valid_id("9007199254740993") == "9007199254740993"
    assert public({"created_by": 9007199254740993, "assigned_to": None}) == {
        "created_by": "9007199254740993",
        "assigned_to": None,
    }
    with pytest.raises(ValueError):
        valid_id(True)


def test_csv_formula_injection_and_nullable_values():
    assert safe_cell("=1+1") == "'=1+1"
    assert safe_cell(None) == ""
    assert safe_cell("hello") == "hello"
