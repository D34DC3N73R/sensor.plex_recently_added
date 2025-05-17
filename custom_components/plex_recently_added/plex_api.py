from pytz import timezone
from xml.etree import ElementTree
import requests
from urllib3.exceptions import InsecureRequestWarning

from homeassistant.core import HomeAssistant
from .const import DEFAULT_PARSE_DICT, USER_AGENT, ACCEPTS
from .parser import parse_data, parse_library
from .tmdb_api import get_tmdb_trailer_url


def check_headers(response):
    if 'text/xml' not in response.headers.get('Content-Type', '') and 'application/xml' not in response.headers.get('Content-Type', ''):
        raise ValueError(f"Expected XML but received different content type: {response.headers.get('Content-Type')}")


class PlexApi:
    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        ssl: bool,
        token: str,
        max: int,
        on_deck: bool,
        host: str,
        port: int,
        section_types: list,
        section_libraries: list,
        exclude_keywords: list
    ):
        self._hass = hass
        self._ssl = 's' if ssl else ''
        self._token = token
        self._max = max
        self._on_deck = on_deck
        self._host = host
        self._port = port
        self._section_types = section_types
        self._section_libraries = section_libraries
        self._exclude_keywords = exclude_keywords
        self._images_base_url = f'/{name.lower() + "_" if len(name) > 0 else ""}plex_recently_added'

    async def update(self):
        info_url = 'http{0}://{1}:{2}'.format(
            self._ssl,
            self._host,
            self._port
        )

        """ Getting the server identifier """
        try:
            info_res = await self._hass.async_add_executor_job(
                requests.get,
                f'{info_url}?X-Plex-Token={self._token}',
                {
                    "headers": {
                        "User-agent": USER_AGENT,
                        "Accept": ACCEPTS,
                    },
                    "timeout": 10
                }
            )
            check_headers(info_res)
            root = ElementTree.fromstring(info_res.text)
            identifier = root.get("machineIdentifier")
        except Exception as e:
            raise FailedToLogin(str(e))

        url_base = f'{info_url}/library/sections'
        all_libraries = f'{url_base}/all'
        recently_added = (url_base + '/{0}/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size={1}')
        on_deck = (url_base + '/{0}/onDeck?X-Plex-Container-Start=0&X-Plex-Container-Size={1}')

        """ Find the ID of all libraries in Plex """
        sections = []
        libs = []
        try:
            libraries = await self._hass.async_add_executor_job(
                requests.get,
                f'{all_libraries}?X-Plex-Token={self._token}',
                {
                    "headers": {
                        "User-agent": USER_AGENT,
                        "Accept": ACCEPTS,
                    },
                    "timeout": 10
                }
            )
            check_headers(libraries)
            root = ElementTree.fromstring(libraries.text)
            for lib in root.findall("Directory"):
                libs.append(lib.get("title"))
                if lib.get("type") in self._section_types and (len(self._section_libraries) == 0 or lib.get("title") in self._section_libraries):
                    sections.append({'type': lib.get("type"), 'key': lib.get("key")})
        except Exception as e:
            raise FailedToLogin(str(e))

        """ Collect all recently added items across libraries """
        all_items = []
        for library in sections:
            recent_or_deck = on_deck if self._on_deck else recently_added
            sub_sec = await self._hass.async_add_executor_job(
                requests.get,
                f'{recent_or_deck.format(library["key"], self._max * 2)}&X-Plex-Token={self._token}',
                {
                    "headers": {
                        "User-agent": USER_AGENT,
                        "Accept": ACCEPTS,
                    },
                    "timeout": 10
                }
            )
            check_headers(sub_sec)
            root = ElementTree.fromstring(sub_sec.text)
            parsed_libs = parse_library(root)

            # Add library type and addedAt to each item
            for item in parsed_libs:
                item['library_type'] = library['type']
                item['addedAt'] = item.get('addedAt', 0)  # Ensure addedAt is present
                all_items.append(item)

        """ Sort and select the most recent item """
        if not all_items:
            return {
                "data": {"all": {"data": [DEFAULT_PARSE_DICT]}},
                "online": True,
                "libraries": libs
            }

        # Sort by addedAt (most recent first)
        most_recent_item = max(all_items, key=lambda x: x['addedAt'])

        # Fetch trailer URL for the most recent item
        item_type = 'movie' if most_recent_item.get('episode') == '' else 'show'
        most_recent_item['trailer'] = await get_tmdb_trailer_url(self._hass, most_recent_item['title'], item_type)

        # Format the most recent item
        parsed_data = parse_data(
            self._hass,
            [most_recent_item],  # Pass single item as a list
            1,  # max=1
            info_url,
            self._token,
            identifier,
            'all',
            self._images_base_url,
            True  # is_all=True
        )

        # Ensure trailer URL is set
        if parsed_data and parsed_data[0].get('trailer') is None:
            parsed_data[0]['trailer'] = await get_tmdb_trailer_url(self._hass, parsed_data[0]['title'], item_type)

        data_out = {
            'all': {'data': [DEFAULT_PARSE_DICT] + parsed_data}
        }

        return {
            "data": data_out,
            "online": True,
            "libraries": libs
        }


class FailedToLogin(Exception):
    "Raised when the Plex user fail to Log-in"
    pass
