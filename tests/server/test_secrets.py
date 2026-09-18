from __future__ import annotations

from bazaar_compute_server.secrets import hash_secret, new_secret, verify_secret


def test_secrets_verify_against_their_own_salt_only() -> None:
    secret = new_secret()
    stored = hash_secret(secret)
    again = hash_secret(secret)

    # case: the same secret hashes differently each time, and both verify
    assert stored != again
    assert stored.startswith("scrypt$32768$8$1$")
    assert verify_secret(secret, stored)
    assert verify_secret(secret, again)

    # case: anything else does not
    assert not verify_secret(secret + "x", stored)
    assert not verify_secret(secret, "not-a-hash")
    assert secret not in stored
