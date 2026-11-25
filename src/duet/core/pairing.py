"""In-memory pairing credentials and revocable sessions for one server run."""

import hmac
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class PairingError(Exception):
    """A credentials or rate-limit failure."""

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class PairingOffer:
    token: str = field(repr=False)
    pin: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    address: str
    expires_at: float


class PairingManager:
    """Manage offers and sessions from a single server event loop."""

    def __init__(
        self,
        *,
        offer_ttl: float = 600,
        session_ttl: float = 8 * 60 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (0 < offer_ttl < float("inf") and 0 < session_ttl < float("inf")):
            raise ValueError(
                "Pairing and session lifetimes must be finite and positive"
            )
        self.offer_ttl = offer_ttl
        self.session_ttl = session_ttl
        self._clock = clock
        self._offer: PairingOffer | None = None
        self._sessions: dict[str, Session] = {}
        self._wrong_pins = 0
        self._locked_until = 0.0

    def issue_offer(self) -> PairingOffer:
        """Replace the current offer without resetting its PIN rate limit."""
        self._offer = PairingOffer(
            secrets.token_urlsafe(32),
            f"{secrets.randbelow(1_000_000):06d}",
            self._clock() + self.offer_ttl,
        )
        return self._offer

    def pair(
        self, *, address: str, token: str | None = None, pin: str | None = None
    ) -> str:
        """Consume an offer and return the secret for its new session."""
        now = self._clock()
        if (token is None) == (pin is None):
            raise PairingError("Supply exactly one pairing credential")
        if pin is not None and now < self._locked_until:
            raise PairingError(
                "PIN pairing is temporarily locked",
                retry_after=math.ceil(self._locked_until - now),
            )
        offer = self._offer
        if offer is None or now >= offer.expires_at:
            raise PairingError("Pairing credentials are invalid or expired")

        supplied = token if token is not None else pin
        expected = offer.token if token is not None else offer.pin
        if (
            not isinstance(supplied, str)
            or not supplied.isascii()
            or not hmac.compare_digest(supplied, expected)
        ):
            if pin is not None:
                self._wrong_pins += 1
                if self._wrong_pins >= 5:
                    self._wrong_pins = 0
                    self._locked_until = now + 60
                    raise PairingError(
                        "PIN pairing is temporarily locked", retry_after=60
                    )
            raise PairingError("Pairing credentials are invalid or expired")

        self._offer = None
        self._wrong_pins = 0
        secret = secrets.token_urlsafe(32)
        self._prune_expired()
        self._sessions[secret] = Session(
            secrets.token_urlsafe(12), address, now + self.session_ttl
        )
        return secret

    def authenticate(self, secret: str | None) -> Session | None:
        if secret is None:
            return None
        session = self._sessions.get(secret)
        if session is not None and self.remaining(session) <= 0:
            del self._sessions[secret]
            return None
        return session

    def remaining(self, session: Session) -> float:
        return max(0.0, session.expires_at - self._clock())

    def devices(self) -> tuple[Session, ...]:
        self._prune_expired()
        return tuple(self._sessions.values())

    def revoke(self, session_id: str) -> bool:
        for secret, session in self._sessions.items():
            if session.id == session_id:
                del self._sessions[secret]
                return True
        return False

    def _prune_expired(self) -> None:
        for secret, session in tuple(self._sessions.items()):
            if self.remaining(session) <= 0:
                del self._sessions[secret]
