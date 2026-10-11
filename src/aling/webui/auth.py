"""Process-local, bounded sessions for the deployment owner's panel."""

from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from secrets import compare_digest, token_urlsafe
from time import monotonic

from aling.core.config import Settings

COOKIE_NAME = "aling_panel_session"
SESSION_SECONDS = 8 * 3600


class LoginRateLimited(PermissionError):
    pass


@dataclass(frozen=True)
class PanelSession:
    session_id: str = field(repr=False)
    csrf_token: str = field(repr=False)
    owner_id: int
    expires_at: float


class PanelAuth:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = monotonic):
        self._token = settings.webui_admin_token.get_secret_value().encode()
        self._owner_id = settings.webui_owner_id
        self._clock = clock
        self._sessions: OrderedDict[str, PanelSession] = OrderedDict()
        # At most 20 failures can enter the window, so source tracking is also bounded.
        self._failures: deque[tuple[float, str]] = deque()

    def check_login_allowed(self, client_ip: str) -> None:
        cutoff = self._clock() - 60
        while self._failures and self._failures[0][0] <= cutoff:
            self._failures.popleft()
        if len(self._failures) >= 20 or sum(ip == client_ip for _, ip in self._failures) >= 5:
            raise LoginRateLimited("Login rate limit reached")

    def record_failure(self, client_ip: str) -> None:
        self.check_login_allowed(client_ip)
        self._failures.append((self._clock(), client_ip))

    def login(self, token: str, client_ip: str) -> PanelSession:
        self.check_login_allowed(client_ip)
        try:
            candidate = token.encode()
        except UnicodeEncodeError:
            candidate = b""
        if not compare_digest(candidate, self._token):
            self.record_failure(client_ip)
            raise PermissionError("Invalid login credentials")
        now = self._clock()
        self._sessions = OrderedDict(
            (key, session) for key, session in self._sessions.items() if session.expires_at > now
        )
        if len(self._sessions) >= 8:
            self._sessions.popitem(last=False)
        session = PanelSession(
            token_urlsafe(32), token_urlsafe(32), self._owner_id, now + SESSION_SECONDS
        )
        self._sessions[session.session_id] = session
        return session

    def authenticate(self, session_id: str) -> PanelSession:
        session = self._sessions.get(session_id)
        if session is None or session.expires_at <= self._clock():
            self.revoke(session_id)
            raise PermissionError("Panel session expired")
        return session

    def revoke(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
