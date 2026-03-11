import logging
import asyncio
import time
import ssl
from contextlib import asynccontextmanager
from datetime import timedelta
from yarl import URL

import aiohttp
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, VERIFY_SSL

_LOGGER = logging.getLogger(__name__)

AROMA_BASE = "https://www.aroma-link.com"


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

        jar = aiohttp.CookieJar(unsafe=True)
        self.session = aiohttp.ClientSession(cookie_jar=jar)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_auth",
            update_interval=timedelta(minutes=15),
        )

    async def async_close(self):
        """Close the dedicated HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()

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
        """Request wrapper with optional SSL fallback."""
        ssl_opt = kwargs.pop("ssl", self.verify_ssl)
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
        login_url = f"{AROMA_BASE}/login"
        data = {"username": self.username, "password": self.password}
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": AROMA_BASE,
            "Referer": f"{AROMA_BASE}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }

        try:
            self.session.cookie_jar.clear()

            self.session.cookie_jar.update_cookies(
                {"languagecode": self.language_code},
                URL(AROMA_BASE),
            )

            _LOGGER.debug(
                "Attempting initial GET to aroma-link.com for cookies.")
            async with self.request("get", f"{AROMA_BASE}/", timeout=10) as initial_response:
                initial_response.raise_for_status()
                _LOGGER.debug(
                    f"Initial GET successful (status {initial_response.status}).")

            _LOGGER.debug(
                f"Attempting login to {login_url} as {self.username}.")
            async with self.request("post", login_url, data=data, headers=headers, timeout=10) as response:
                response_text = await response.text()
                _LOGGER.debug(f"Login response status: {response.status}")

                all_cookies = {
                    c.key: c.value
                    for c in self.session.cookie_jar
                }
                _LOGGER.debug("Cookies after login: %s", list(all_cookies.keys()))

                if response.status == 200:
                    jsessionid_found = await self._extract_jsessionid(response, response_text)

                    if jsessionid_found:
                        self.jsessionid = jsessionid_found
                        self._last_login_time = time.time()
                        _LOGGER.info(
                            f"Successfully logged in as {self.username}.")
                        return True
                    else:
                        _LOGGER.error(
                            "No JSESSIONID cookie found in response.")
                        return False
                else:
                    _LOGGER.error(
                        f"Login failed with status code: {response.status}.")
                    return False
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout during login process.")
            return False
        except Exception as e:
            _LOGGER.error(f"Login error: {e}", exc_info=True)
            return False

    async def _extract_jsessionid(self, response, response_text):
        """Extract JSESSIONID from various sources."""
        jsessionid = None

        filtered_cookies = self.session.cookie_jar.filter_cookies(response.url)

        if "JSESSIONID" in filtered_cookies:
            jsessionid_morsel = filtered_cookies["JSESSIONID"]
            jsessionid = jsessionid_morsel.value
            _LOGGER.debug(
                f"Found JSESSIONID in cookie jar: {jsessionid[:5]}...")
            return jsessionid

        if 'Set-Cookie' in response.headers:
            cookie_header = response.headers['Set-Cookie']
            if 'JSESSIONID=' in cookie_header:
                try:
                    start = cookie_header.index('JSESSIONID=') + 11
                    end = cookie_header.index(
                        ';', start) if ';' in cookie_header[start:] else len(cookie_header)
                    jsessionid = cookie_header[start:end]
                    _LOGGER.debug(
                        f"Extracted JSESSIONID from header: {jsessionid[:5]}...")
                    self.session.cookie_jar.update_cookies(
                        {"JSESSIONID": jsessionid},
                        URL(AROMA_BASE),
                    )
                    return jsessionid
                except Exception as e:
                    _LOGGER.error(
                        f"Error extracting JSESSIONID from header: {e}")

        if "success" in response_text.lower():
            _LOGGER.warning(
                "Login appears successful based on response text, but no JSESSIONID found. Using temporary ID.")
            jsessionid = f"temp_login_success_{time.time()}"
            return jsessionid

        return None
