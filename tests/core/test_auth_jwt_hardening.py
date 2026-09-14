from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.core.config import settings
from app.core.security import (
    ALGORITHM,
    TOKEN_ISSUER,
    TOKEN_VERSION,
    create_access_token,
    derive_token_signing_key,
    token_audience,
    verify_token,
)


def test_versioned_jwt_has_purpose_bound_claims_and_key() -> None:
    subject = uuid4()
    token = create_access_token(subject)
    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})

    assert header == {"alg": ALGORITHM, "kid": "klm-access-v2", "typ": "JWT"}
    assert payload["sub"] == str(subject)
    assert payload["iss"] == TOKEN_ISSUER
    assert payload["aud"] == token_audience("access")
    assert payload["token_version"] == TOKEN_VERSION
    assert derive_token_signing_key("access") != derive_token_signing_key("refresh")
    assert verify_token(token) == payload


def test_versioned_jwt_rejects_wrong_audience_and_wrong_purpose_key() -> None:
    token = create_access_token(uuid4())
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["aud"] = token_audience("refresh")
    wrong_audience = jwt.encode(
        payload,
        derive_token_signing_key("access"),
        algorithm=ALGORITHM,
        headers={"kid": "klm-access-v2", "typ": "JWT"},
    )
    assert verify_token(wrong_audience) is None

    payload["aud"] = token_audience("access")
    wrong_key = jwt.encode(
        payload,
        derive_token_signing_key("refresh"),
        algorithm=ALGORITHM,
        headers={"kid": "klm-access-v2", "typ": "JWT"},
    )
    assert verify_token(wrong_key) is None


def test_current_legacy_hs256_tokens_remain_compatible() -> None:
    subject = uuid4()
    legacy_token = jwt.encode(
        {
            "sub": str(subject),
            "jti": str(uuid4()),
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "token_type": "access",
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )

    payload = verify_token(legacy_token)

    assert payload is not None
    assert payload["sub"] == str(subject)
    assert payload["auth_epoch"] == 0
    assert payload.get("iss") is None


def test_versioned_token_cannot_omit_auth_epoch() -> None:
    token = create_access_token(uuid4())
    payload = jwt.decode(token, options={"verify_signature": False})
    payload.pop("auth_epoch")
    missing_epoch = jwt.encode(
        payload,
        derive_token_signing_key("access"),
        algorithm=ALGORITHM,
        headers={"kid": "klm-access-v2", "typ": "JWT"},
    )

    assert verify_token(missing_epoch) is None


def test_algorithm_none_is_rejected_before_claim_validation() -> None:
    unsigned = jwt.encode(
        {
            "sub": str(uuid4()),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "token_type": "access",
        },
        key="",
        algorithm="none",
    )

    assert verify_token(unsigned) is None
