import logging
import asyncio
import time
import ssl
from contextlib import asynccontextmanager
from datetime import timedelta
import aiohttp
from yarl import URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, VERIFY_SSL

_LOGGER = logging.getLogger(__name__)

AROMA_BASE = "https://www.aroma-link.com"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class AromaLinkAuthCoordinator(DataUpdateCoordinator):
    """Coordinator for handling authentication and session management."""

    def __init__(self, hass, username, password, verify_ssl=VERIFY_SSL, allow_ssl_fallback=True):
        """Initialize the auth coordinator."""
        self.hass = hass
        self.username = username
        self.password = password
        self.jsessionid = None
        self.language_code = "EN"
        self._last_login_time = 0
        self.verify_ssl = verify_ssl
        self.allow_ssl_fallback = allow_ssl_fallback
        self._ssl_fallback_notified = False

        self.session = async_get_clientsession(hass)
        # Cookie names that belong to aroma-link (populated during login).
        # Used to build Cookie headers without leaking other integrations' cookies.
        self._aroma_cookie_names: set[str] = {"languagecode"}

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_auth",
            update_interval=timedelta(minutes=15),
        )

    async def async_close(self):
        """Nothing to close -- we use the shared HA session."""

    async def _async_update_data(self):
        """Fetch authentication data."""
        await self._ensure_login()
        return {"jsessionid": self.jsessionid, "last_login": self._last_login_time}

    def _notify_ssl_fallback(self, error):
        """Notify user that SSL verification was disabled after failure."""
        if self._ssl_fallback_notified:
            return
        self._ssl_fallback_notified = True
        _LOGGER.warning(
            "SSL verification failed (%s). Falling back to insecure SSL.",
            error,
        )
        try:
            self.hass.components.persistent_notification.async_create(
                "SSL verification failed for Aroma-Link. The integration "
                "is now bypassing certificate checks to keep working. "
                "You can re-enable verification in integration options.",
                title="Aroma-Link SSL Fallback",
                notification_id=f"{DOMAIN}_ssl_fallback",
            )
        except Exception as exc:
            _LOGGER.debug("Could not create SSL fallback notification: %s", exc)

    @asynccontextmanager
    async def request(self, method, url, **kwargs):
        """Request wrapper with User-Agent and SSL fallback.

        Cookies are handled automatically by aiohttp's cookie jar —
        no manual Cookie header is constructed.
        """
        ssl_opt = kwargs.pop("ssl", self.verify_ssl)

        hdrs = dict(kwargs.pop("headers", None) or {})
        hdrs.setdefault("User-Agent", _BROWSER_UA)
        # Strip any caller-provided Cookie header so the jar is authoritative.
        hdrs.pop("Cookie", None)

        kwargs["headers"] = hdrs

        # Log what the jar will send for debugging.
        jar_cookies = self.session.cookie_jar.filter_cookies(URL(url))
        _LOGGER.debug(
            "request(%s %s) jar cookies: %s",
            method.upper(), url,
            {k: v.value[:20] for k, v in jar_cookies.items()} if jar_cookies else "(none)",
        )

        try:
            async with self.session.request(
                method, url, ssl=ssl_opt, **kwargs
            ) as response:
                yield response
        except (
            aiohttp.ClientConnectorCertificateError,
            aiohttp.ClientSSLError,
            ssl.SSLCertVerificationError,
        ) as err:
            if self.allow_ssl_fallback and self.verify_ssl:
                self.verify_ssl = False
                self._notify_ssl_fallback(err)
                async with self.session.request(
                    method, url, ssl=False, **kwargs
                ) as response:
                    yield response
            else:
                raise

    async def _ensure_login(self):
        """Ensure we have a valid session, login if needed."""
        current_time = time.time()
        session_age = current_time - self._last_login_time

        if self.jsessionid is None or self.jsessionid.startswith("temp_") or session_age > 1200:
            _LOGGER.debug(
                "Session expired, temporary, or not established. Attempting login.")
            login_success = await self._login()
            if not login_success:
                _LOGGER.error("Failed to login during ensure_login.")
                raise UpdateFailed(
                    "Authentication failed, cannot update auth state.")
        return True

    async def _login(self):
        """Login to Aroma-Link and get session ID."""
        self.jsessionid = None
        login_url = f"{AROMA_BASE}/login"
        data = {"username": self.username, "password": self.password}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": AROMA_BASE,
            "Referer": f"{AROMA_BASE}/",
        }

        try:
            # Snapshot cookie names BEFORE aroma-link requests so we can
            # identify which new cookies come from aroma-link.
            pre_cookie_names = {c.key for c in self.session.cookie_jar}

            _LOGGER.debug(
                "Attempting initial GET to aroma-link.com for cookies.")
            async with self.session.get(
                f"{AROMA_BASE}/",
                headers={"User-Agent": _BROWSER_UA},
                ssl=self.verify_ssl,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as initial_response:
                initial_response.raise_for_status()
                _LOGGER.debug(
                    "Initial GET successful (status %s).", initial_response.status)
                # Log Set-Cookie headers so we can debug cookie domain/path attrs.
                for sc in initial_response.headers.getall("Set-Cookie", []):
                    _LOGGER.debug("Initial GET Set-Cookie: %s", sc)

                # Ensure languagecode is in the jar so it's sent on every request.
                self.session.cookie_jar.update_cookies(
                    {"languagecode": self.language_code},
                    URL(AROMA_BASE),
                )

                jsessionid = self._cookie_from_jar("JSESSIONID", initial_response.url)
                if jsessionid:
                    self.jsessionid = jsessionid
                    _LOGGER.debug("Got JSESSIONID from initial GET: %s...", jsessionid[:5])

            _LOGGER.debug(
                "Attempting login to %s as %s.", login_url, self.username)
            async with self.request("post", login_url, data=data, headers=headers, timeout=10) as response:
                response_text = await response.text()
                _LOGGER.debug("Login response status: %s", response.status)
                _LOGGER.debug("Login response body (first 300): %s", response_text[:300])
                _LOGGER.debug("Login response headers: %s", dict(response.headers))

                if response.status == 200:
                    new_jsessionid = self._cookie_from_jar("JSESSIONID", response.url)
                    if new_jsessionid:
                        self.jsessionid = new_jsessionid

                    # Identify cookies added by aroma-link requests.
                    post_cookie_names = {c.key for c in self.session.cookie_jar}
                    new_names = post_cookie_names - pre_cookie_names
                    self._aroma_cookie_names.update(new_names)
                    self._aroma_cookie_names.add("JSESSIONID")
                    _LOGGER.debug(
                        "Aroma-link cookie names: %s (new: %s)",
                        self._aroma_cookie_names, new_names,
                    )
                    # Log full cookie attributes for debugging.
                    for c in self.session.cookie_jar:
                        _LOGGER.debug(
                            "Jar cookie: %s=%s (domain=%r, path=%r)",
                            c.key, c.value[:20] if c.value else "",
                            c.get("domain", ""), c.get("path", ""),
                        )

                    if self.jsessionid:
                        _LOGGER.debug("JSESSIONID: %s...", self.jsessionid[:5])
                        self._last_login_time = time.time()
                        _LOGGER.info(
                            "Successfully logged in as %s.", self.username)

                        await self._warm_up_session()
                        return True
                    else:
                        _LOGGER.error(
                            "No JSESSIONID cookie found in response.")
                        return False
                else:
                    _LOGGER.error(
                        "Login failed with status code: %s.", response.status)
                    return False
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during login process.")
            return False
        except Exception as e:
            _LOGGER.error("Login error: %s", e, exc_info=True)
            return False

    async def _warm_up_session(self):
        """Navigate to the device list page after login.

        The Aroma-Link server requires a page navigation after login before
        API calls will succeed (sets server-side session context / additional
        cookies).  This mirrors what the browser does.
        """
        pre = {c.key for c in self.session.cookie_jar}
        try:
            async with self.request(
                "get",
                f"{AROMA_BASE}/device/list",
                headers={"Referer": f"{AROMA_BASE}/"},
                timeout=10,
            ) as resp:
                body = await resp.text()
                final_url = str(resp.url)
                if "/login" in final_url:
                    _LOGGER.warning(
                        "Session warm-up redirected to login — session invalid."
                    )
                    self.jsessionid = None
                    return
                # Capture any new cookies set during warm-up.
                post = {c.key for c in self.session.cookie_jar}
                self._aroma_cookie_names.update(post - pre)
                _LOGGER.debug(
                    "Session warm-up GET /device/list status: %s, "
                    "final_url: %s, aroma cookie names: %s",
                    resp.status,
                    final_url,
                    self._aroma_cookie_names,
                )
                # Re-read JSESSIONID in case the server rotated it.
                new_jsessionid = self._cookie_from_jar("JSESSIONID", resp.url)
                if new_jsessionid and new_jsessionid != self.jsessionid:
                    _LOGGER.debug(
                        "JSESSIONID rotated during warm-up: %s... -> %s...",
                        self.jsessionid[:5] if self.jsessionid else "None",
                        new_jsessionid[:5],
                    )
                    self.jsessionid = new_jsessionid
                    self._last_login_time = time.time()
        except Exception as exc:
            _LOGGER.debug("Session warm-up failed (non-fatal): %s", exc)

    def _cookie_from_jar(self, name, url):
        """Read a single cookie from the shared session's cookie jar."""
        try:
            filtered = self.session.cookie_jar.filter_cookies(url)
            if name in filtered:
                return filtered[name].value
        except Exception:
            pass
        return None
