"""Bind a freshly constructed account session to its expected public identity."""
import re
from urllib.parse import unquote

from .store import ACCOUNT, SessionUnavailable


TRUSTED_DOMAINS = frozenset({'x.com', '.x.com', 'api.x.com'})
IDENTIFIER = re.compile(r'[1-9][0-9]{0,19}\Z')
MARKER = re.compile(r'u=([1-9][0-9]{0,19})\Z')


def cookies(connection):
    try:
        return list(connection.http.cookies.jar)
    except (AttributeError, TypeError):
        raise SessionUnavailable('Account identity cookie jar is unavailable') from None


def public_identity(user):
    ident = str(getattr(user, 'id', ''))
    handle = getattr(user, 'screen_name', None)
    if (not IDENTIFIER.fullmatch(ident) or not isinstance(handle, str)
            or handle.casefold() != ACCOUNT):
        raise SessionUnavailable('Account identity does not match the configured account')
    return ident


def server_identity(jar):
    identities = set()
    for cookie in jar:
        if cookie.name != 'twid' or cookie.domain not in TRUSTED_DOMAINS:
            continue
        value = cookie.value
        if not isinstance(value, str):
            raise SessionUnavailable('Server account identity marker is malformed')
        value = unquote(value)
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        match = MARKER.fullmatch(value)
        if not match:
            raise SessionUnavailable('Server account identity marker is malformed')
        identities.add(match.group(1))
    if len(identities) > 1:
        raise SessionUnavailable('Server account identity markers disagree')
    return next(iter(identities), None)


async def authenticated_user(connection):
    """Read identity only; never manufacture cookies or suppress access failures.

    The caller must supply the fresh store.client(), which seeds only auth_token
    and ct0. A twid that appears during the normal X profile request therefore
    comes from a server response, not a guessed or configured account ID.
    """
    if getattr(connection, '_act_as', None) is not None:
        raise SessionUnavailable('Delegated account identity is not supported')
    if any(cookie.name == 'twid' for cookie in cookies(connection)):
        raise SessionUnavailable('Account identity requires a fresh session')
    profile = await connection.get_user_by_screen_name(ACCOUNT)
    expected = public_identity(profile)
    observed = server_identity(cookies(connection))
    if observed is not None:
        if observed != expected:
            raise SessionUnavailable('Server account identity does not match the configured account')
        print('Matched server-issued account identity.', flush=True)
        return profile
    print('No identity marker; using settings.', flush=True)
    # This is the original identity API, used only when no fresh marker exists.
    # Authentication, permission, lock, rate-limit and transport errors propagate.
    verified = await connection.user()
    if public_identity(verified) != expected:
        raise SessionUnavailable('Settings account identity does not match the configured account')
    return verified
