import voluptuous as vol
import logging
import json
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import aiohttp
import ssl

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_DEVICE_ID,
    CONF_POLL_INTERVAL,
    CONF_DEBUG_LOGGING,
    CONF_VERIFY_SSL,
    CONF_ALLOW_SSL_FALLBACK,
    DEFAULT_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_VERIFY_SSL,
    DEFAULT_ALLOW_SSL_FALLBACK,
)

_LOGGER = logging.getLogger(__name__)

class AromaLinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aroma-Link."""

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize the config flow."""
        self._username = None
        self._password = None
        self._jsessionid = None
        self._devices = []
        self._reauth_entry = None
        self._show_ssl_option = False
        self._ssl_error = False
        
    async def async_step_user(self, user_input=None):
        """Handle the initial step - username and password."""
        errors = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            verify_ssl = user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            allow_ssl_fallback = user_input.get(CONF_ALLOW_SSL_FALLBACK, DEFAULT_ALLOW_SSL_FALLBACK)
            
            _LOGGER.debug(f"Setting up with username: {username}")
            
            # Try to authenticate
            session, jsessionid, devices, error = await self._authenticate(
                username, password, verify_ssl=verify_ssl
            )
            
            if jsessionid:
                self._username = username
                self._password = password
                self._jsessionid = jsessionid
                self._devices = devices
                
                # Create a unique ID based on the username
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_configured()
                
                # If we have devices, create entry with all devices
                if devices:
                    device_names = [d.get("deviceName", f"Device {d['deviceId']}") for d in devices]
                    _LOGGER.info(f"Adding {len(devices)} devices: {device_names}")
                    
                    return self.async_create_entry(
                        title=f"Aroma-Link ({username})",
                        data={
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_VERIFY_SSL: verify_ssl,
                            CONF_ALLOW_SSL_FALLBACK: allow_ssl_fallback,
                            "devices": [
                                {
                                    CONF_DEVICE_ID: str(device["deviceId"]),
                                    "device_name": device.get("deviceName", f"Device {device['deviceId']}")
                                }
                                for device in devices
                            ]
                        }
                    )
                else:
                    # No devices found, show an error
                    errors["base"] = "no_devices"
            else:
                if error == "ssl_error" and verify_ssl:
                    self._show_ssl_option = True
                    self._ssl_error = True
                    errors["base"] = "ssl_error"
                else:
                    errors["base"] = "cannot_connect"

        # Show the initial form for username/password
        schema_fields = {
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        }
        if self._show_ssl_option:
            default_verify = False if self._ssl_error else DEFAULT_VERIFY_SSL
            schema_fields[vol.Optional(CONF_VERIFY_SSL, default=default_verify)] = bool
        schema_fields[
            vol.Optional(
                CONF_ALLOW_SSL_FALLBACK,
                default=DEFAULT_ALLOW_SSL_FALLBACK,
            )
        ] = bool

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AromaLinkOptionsFlowHandler(config_entry)

    async def _authenticate(self, username, password, verify_ssl=True):
        """Authenticate with Aroma-Link API and retrieve device list.
        
        Returns:
            tuple: (session, jsessionid, devices)
            - session: The authenticated aiohttp ClientSession
            - jsessionid: The JSESSIONID cookie value if authentication successful, None otherwise
            - devices: List of discovered devices, or empty list if none found
        """
        session = async_get_clientsession(self.hass)
        jsessionid = None
        devices = []
        error = None
        
        login_url = "https://www.aroma-link.com/login"
        
        data = {
            "username": username,
            "password": password
        }
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.aroma-link.com",
            "Referer": "https://www.aroma-link.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        try:
            # First, do an initial GET to establish cookies
            _LOGGER.debug("Attempting initial GET to aroma-link.com for cookies.")
            try:
                async with session.get("https://www.aroma-link.com/", timeout=10, ssl=verify_ssl) as initial_response:
                    initial_response.raise_for_status()
                    _LOGGER.debug(f"Initial GET successful (status {initial_response.status}).")
            except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError, ssl.SSLCertVerificationError) as e:
                _LOGGER.warning(f"SSL verification failed during initial GET: {e}")
                return session, None, [], "ssl_error"
            except Exception as e:
                _LOGGER.warning(f"Initial GET request failed, but continuing: {e}")
            
            # Now attempt login to get JSESSIONID
            _LOGGER.debug(f"Attempting login for {username}")
            
            async with session.post(login_url, data=data, headers=headers, ssl=verify_ssl) as response:
                _LOGGER.debug(f"Login response status: {response.status}")
                
                response_text = await response.text()
                _LOGGER.debug(f"Login response body: {response_text[:200]}...")
                
                if response.status == 200:
                    # Try multiple methods to get the JSESSIONID cookie
                    jsessionid = await self._extract_jsessionid(session, response, response_text, username)
                    
                    if jsessionid:
                        _LOGGER.info(f"Successfully logged in as {username}")
                        
                        # Now fetch device list
                        _LOGGER.debug("Fetching device list")
                        devices = await self._fetch_device_list(session, jsessionid, verify_ssl=verify_ssl)
                        
                        _LOGGER.info(f"Found {len(devices)} devices for {username}")
                        return session, jsessionid, devices, None
                    else:
                        _LOGGER.error("No JSESSIONID cookie found. Authentication failed.")
                        
                        # Check response for potential error messages
                        if "error" in response_text.lower() or "invalid" in response_text.lower():
                            try:
                                # Try to extract error message from response
                                error_msg = "Unknown error"
                                if "msg" in response_text:
                                    try:
                                        json_resp = json.loads(response_text)
                                        if "msg" in json_resp:
                                            error_msg = json_resp["msg"]
                                    except:
                                        pass
                                _LOGGER.error(f"Server returned error: {error_msg}")
                            except:
                                _LOGGER.error("Could not parse error message from response")
                else:
                    _LOGGER.error(f"Login failed with status code: {response.status}")
        except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError, ssl.SSLCertVerificationError) as e:
            _LOGGER.warning(f"SSL verification failed during authentication: {e}")
            error = "ssl_error"
        except aiohttp.ClientError as e:
            _LOGGER.error(f"Network error while authenticating: {e}")
        except json.JSONDecodeError as e:
            _LOGGER.error(f"Error parsing JSON response: {e}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error during authentication: {e}", exc_info=True)
        
        return session, None, [], error
        
    async def _extract_jsessionid(self, session, response, response_text, username):
        """Extract JSESSIONID using multiple methods."""
        jsessionid = None
        
        # Method 1: Try to get JSESSIONID from cookie jar
        filtered_cookies = session.cookie_jar.filter_cookies(response.url)
        _LOGGER.debug(f"Filtered cookies from jar: {filtered_cookies}")
        
        if "JSESSIONID" in filtered_cookies:
            jsessionid_morsel = filtered_cookies["JSESSIONID"]
            jsessionid = jsessionid_morsel.value
            _LOGGER.debug(f"Found JSESSIONID in cookie jar: {jsessionid[:5]}...")
            return jsessionid
        
        # Method 2: If not found in jar, check response headers
        if 'Set-Cookie' in response.headers:
            _LOGGER.debug(f"Looking for JSESSIONID in Set-Cookie header")
            cookie_header = response.headers['Set-Cookie']
            if 'JSESSIONID=' in cookie_header:
                try:
                    start = cookie_header.index('JSESSIONID=') + 11
                    end = cookie_header.index(';', start) if ';' in cookie_header[start:] else len(cookie_header)
                    jsessionid = cookie_header[start:end]
                    _LOGGER.debug(f"Extracted JSESSIONID from header: {jsessionid[:5]}...")
                    return jsessionid
                except Exception as e:
                    _LOGGER.error(f"Error extracting JSESSIONID from header: {e}")
        
        # Method 3: Check if login was successful from response text
        if "success" in response_text.lower():
            _LOGGER.warning("Login appears successful based on response text, but no JSESSIONID found. Using temporary ID.")
            jsessionid = f"temp_login_success_{username}"
            return jsessionid
            
        return None
        
    async def _fetch_device_list(self, session, jsessionid, verify_ssl=True):
        """Fetch the list of devices for the authenticated user."""
        language_code = "EN"
        device_list_url = "https://www.aroma-link.com/device/list/v2?limit=10&offset=0&selectUserId=&groupId=&deviceName=&imei=&deviceNo=&workStatus=&continentId=&countryId=&areaId=&sort=&order="
        
        devices = []
        
        try:
            # First do an initial page load to ensure cookies are set properly
            try:
                _LOGGER.debug("Making initial request to device page")
                async with session.get("https://www.aroma-link.com/device/list", timeout=10, ssl=verify_ssl) as init_response:
                    _LOGGER.debug(f"Initial page request status: {init_response.status}")
            except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError, ssl.SSLCertVerificationError) as e:
                _LOGGER.warning(f"SSL verification failed during device page request: {e}")
                raise
            except Exception as e:
                _LOGGER.warning(f"Initial page request failed, but continuing: {e}")
            
            device_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.aroma-link.com/device/list",
                "Cookie": f"languagecode={language_code}; JSESSIONID={jsessionid}"
            }
            
            _LOGGER.debug(f"Fetching device list with URL: {device_list_url}")
            
            async with session.get(device_list_url, headers=device_headers, ssl=verify_ssl) as device_response:
                _LOGGER.debug(f"Device list response status: {device_response.status}")
                
                if device_response.status == 200:
                    device_response_text = await device_response.text()
                    _LOGGER.debug(f"Device list response (first 200 chars): {device_response_text[:200]}")
                    
                    try:
                        device_data = json.loads(device_response_text)
                        
                        if "rows" in device_data and device_data["rows"]:
                            device_ids = [d.get("deviceId") for d in device_data["rows"]]
                            _LOGGER.info(f"Found devices: {device_ids}")
                            return device_data["rows"]
                        else:
                            _LOGGER.warning("No devices found in the account")
                    except json.JSONDecodeError as e:
                        _LOGGER.error(f"Failed to parse device list response as JSON: {e}")
                else:
                    _LOGGER.error(f"Device list request failed with status: {device_response.status}")
        except Exception as e:
            _LOGGER.error(f"Error fetching device list: {e}", exc_info=True)
            
        return devices


class AromaLinkOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Aroma-Link options: general settings + per-device gates."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry
        self._gate_device_id = None

    def _devices(self):
        devices = self._config_entry.data.get("devices", [])
        return [
            (str(d["device_id"]), d.get("device_name", f"Device {d['device_id']}"))
            for d in devices
        ]

    async def async_step_init(self, user_input=None):
        """Top-level menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "gates"],
        )

    async def async_step_general(self, user_input=None):
        """General options (poll interval, SSL, logging)."""
        if user_input is not None:
            new_options = {**self._config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        options = self._config_entry.options

        current_poll = options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_SECONDS)
        if current_poll <= 30:
            # Migration from old minutes-based config
            current_poll = current_poll * 60

        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=current_poll,
                        description={"suggested_value": current_poll},
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL_SECONDS, max=MAX_POLL_INTERVAL_SECONDS)
                    ),
                    vol.Optional(
                        CONF_VERIFY_SSL,
                        default=options.get(
                            CONF_VERIFY_SSL,
                            self._config_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_ALLOW_SSL_FALLBACK,
                        default=options.get(
                            CONF_ALLOW_SSL_FALLBACK,
                            self._config_entry.data.get(
                                CONF_ALLOW_SSL_FALLBACK, DEFAULT_ALLOW_SSL_FALLBACK
                            ),
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_DEBUG_LOGGING,
                        default=options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING),
                    ): bool,
                }
            ),
            description_placeholders={
                "min_poll": str(MIN_POLL_INTERVAL_SECONDS),
                "max_poll": str(MAX_POLL_INTERVAL_SECONDS),
            },
        )

    async def async_step_gates(self, user_input=None):
        """Pick which device's gates to edit."""
        devices = self._devices()
        if not devices:
            return self.async_abort(reason="no_devices")
        if len(devices) == 1:
            self._gate_device_id = devices[0][0]
            return await self.async_step_gates_device()

        if user_input is not None:
            self._gate_device_id = user_input["device"]
            return await self.async_step_gates_device()

        return self.async_show_form(
            step_id="gates",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): vol.In(
                        {dev_id: name for dev_id, name in devices}
                    ),
                }
            ),
        )

    async def async_step_gates_device(self, user_input=None):
        """Edit one device's gates (HVAC / occupancy / motion / delay)."""
        device_id = self._gate_device_id
        gates_all = dict(self._config_entry.options.get("gates") or {})
        current = dict(gates_all.get(device_id) or {})

        if user_input is not None:
            gates_all[device_id] = {
                "climate_entity": user_input.get("climate_entity") or None,
                "occupancy_entity": user_input.get("occupancy_entity") or None,
                "motion_entities": user_input.get("motion_entities") or [],
                "hvac_on_delay_minutes": user_input.get("hvac_on_delay_minutes", 1),
                "off_delay_minutes": user_input.get("off_delay_minutes", 2),
            }
            new_options = {**self._config_entry.options, "gates": gates_all}
            return self.async_create_entry(title="", data=new_options)

        schema = {
            vol.Optional(
                "climate_entity",
                description={"suggested_value": current.get("climate_entity")},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            vol.Optional(
                "occupancy_entity",
                description={"suggested_value": current.get("occupancy_entity")},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Optional(
                "motion_entities",
                description={"suggested_value": current.get("motion_entities") or []},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Optional(
                "hvac_on_delay_minutes",
                default=current.get("hvac_on_delay_minutes", 1),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
            vol.Optional(
                "off_delay_minutes",
                default=current.get("off_delay_minutes", 2),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=60)),
        }

        device_name = dict(self._devices()).get(device_id, device_id)
        return self.async_show_form(
            step_id="gates_device",
            data_schema=vol.Schema(schema),
            description_placeholders={"device_name": device_name},
        )
