"""Einheitentests für die Passwort-Schicht (app.auth.passwords)."""

from __future__ import annotations

from argon2 import PasswordHasher as Argon2

from app.auth.passwords import Argon2PasswordHasher, PasswordHasher, dummy_hash


def test_hash_is_argon2id_and_verifies() -> None:
    hasher: PasswordHasher = Argon2PasswordHasher()
    hashed = hasher.hash("ein-langes-passwort")
    assert hashed.startswith("$argon2id$")
    assert "ein-langes-passwort" not in hashed
    assert hasher.verify(hashed, "ein-langes-passwort") is True
    assert hasher.verify(hashed, "falsch-falsch-falsch") is False


def test_hash_is_salted() -> None:
    hasher = Argon2PasswordHasher()
    assert hasher.hash("gleiches-passwort") != hasher.hash("gleiches-passwort")


def test_verify_returns_false_for_garbage_hashes() -> None:
    hasher = Argon2PasswordHasher()
    for garbage in ("!", "", "$argon2id$kaputt", "klartext"):
        assert hasher.verify(garbage, "irgendwas-langes") is False


def test_needs_rehash_detects_old_parameters() -> None:
    weak = Argon2(time_cost=1, memory_cost=8, parallelism=1).hash("passwort-12345")
    hasher = Argon2PasswordHasher()
    assert hasher.needs_rehash(weak) is True
    assert hasher.needs_rehash(hasher.hash("passwort-12345")) is False
    assert hasher.needs_rehash("!") is True  # unlesbarer Hash gilt als veraltet


def test_dummy_hash_uses_current_parameters_and_never_matches() -> None:
    hasher = Argon2PasswordHasher()
    assert dummy_hash(hasher).startswith("$argon2id$")
    assert hasher.needs_rehash(dummy_hash(hasher)) is False
    assert dummy_hash(hasher) == dummy_hash(hasher)  # fest, nicht pro Aufruf neu
    assert hasher.verify(dummy_hash(hasher), "") is False
