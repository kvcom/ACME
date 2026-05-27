from jose import jwt


def decode_token(token: str) -> dict:
    return jwt.get_unverified_claims(token)
