from dataclasses import dataclass

from fastapi import Header, HTTPException

from acme_app.auth.jwt_validator import decode_token


@dataclass
class CurrentUser:
    subject: str
    username: str
    roles: list[str]


async def get_current_user(authorization: str = Header(default='')) -> CurrentUser:
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing bearer token')
    payload = decode_token(authorization.removeprefix('Bearer ').strip())
    roles = [r for r in payload.get('realm_access', {}).get('roles', []) if r in {'sales_user', 'support_user', 'admin'}]
    if not roles:
        raise HTTPException(status_code=403, detail='No supported role in token')
    return CurrentUser(str(payload.get('sub', 'unknown')), str(payload.get('preferred_username', 'unknown')), roles)
