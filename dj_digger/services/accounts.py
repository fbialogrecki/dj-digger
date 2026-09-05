"""Account and preference I/O used by both interactive front ends."""

from copy import copy
from dataclasses import dataclass
from threading import Event, Lock

from .. import auth, browser
from ..diagnostics import log_safe_text


@dataclass(frozen=True)
class AuthenticationResult:
    token: str | None = None
    error: str = ""


@dataclass(frozen=True)
class GateProfileAnswer:
    name: str
    email: str


class AccountService:
    def __init__(self, config, client_id, worker_scope):
        self.config = config
        self._preferences_lock = Lock()
        self._auth_settled = Event()
        self._auth_settled.set()
        self.client_id = client_id
        self.worker_scope = worker_scope

    def begin_authentication(self):
        self._auth_settled.clear()

    def wait_authentication(self):
        self._auth_settled.wait()

    def authenticate(self, method, token, cancel, status):
        try:
            with self.worker_scope():
                try:
                    if method == "browser":
                        result = auth.login_with_chromium(self.client_id(), cancel=cancel, status=status)
                    else:
                        result = auth.verify_and_save(token, self.client_id())
                    return AuthenticationResult(token=result[0])
                except auth.SoundCloudAuthCancelled:
                    return AuthenticationResult()
                except auth.SoundCloudAuthError as exc:
                    return AuthenticationResult(error=log_safe_text(exc))
        finally:
            self._auth_settled.set()

    def save_profile(self, answer: GateProfileAnswer):
        self.save_preferences({"user_name": answer.name, "user_email": answer.email})

    def save_preferences(self, values):
        with self.worker_scope(), self._preferences_lock:
            pending = copy(self.config)
            for key, value in values.items():
                setattr(pending, key, value)
            pending.save()
            for key, value in values.items():
                setattr(self.config, key, value)

    def browser_choices(self):
        return browser.available_browsers(), browser.resolve_choice(self.config.browser)
