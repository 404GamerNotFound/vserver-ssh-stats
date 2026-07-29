"""Update platform for VServer SSH Stats: reports newer releases published on GitHub."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.update import UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.loader import async_get_integration

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

RELEASES_API_URL = (
    "https://api.github.com/repos/404GamerNotFound/vserver-ssh-stats/releases/latest"
)
RELEASE_HTML_URL = "https://github.com/404GamerNotFound/vserver-ssh-stats/releases/latest"
UPDATE_CHECK_INTERVAL = timedelta(hours=24)
REQUEST_TIMEOUT = 10


def _parse_latest_version(tag_name: Any) -> str | None:
    """Return a bare version string from a GitHub release tag, or None."""

    tag = str(tag_name or "").strip()
    if tag.startswith("v"):
        tag = tag[1:]
    return tag or None


class VServerReleaseCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the GitHub releases API for the latest published integration version."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the release-check coordinator."""

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_release_check",
            update_interval=UPDATE_CHECK_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest release tag and URL from GitHub."""

        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await session.get(
                    RELEASES_API_URL,
                    headers={"Accept": "application/vnd.github+json"},
                )
                if response.status != 200:
                    raise UpdateFailed(
                        f"GitHub release check returned HTTP {response.status}"
                    )
                payload = await response.json(content_type=None)
        except TimeoutError as err:
            raise UpdateFailed("Timed out checking for a new release") from err
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(str(err)) from err

        return {
            "latest_version": _parse_latest_version(payload.get("tag_name")),
            "release_url": payload.get("html_url") or RELEASE_HTML_URL,
            "release_summary": payload.get("name") or None,
        }


class VServerIntegrationUpdateEntity(
    CoordinatorEntity[VServerReleaseCoordinator], UpdateEntity
):
    """Report when a newer VServer SSH Stats release is published on GitHub."""

    _attr_has_entity_name = True
    _attr_name = "Update"
    _attr_title = "VServer SSH Stats"

    def __init__(
        self,
        coordinator: VServerReleaseCoordinator,
        entry: ConfigEntry,
        installed_version: str,
    ) -> None:
        """Initialize the integration update entity."""

        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_integration_update"
        self._attr_installed_version = installed_version
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="VServer SSH Stats",
            manufacturer="404GamerNotFound",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://github.com/404GamerNotFound/vserver-ssh-stats",
        )

    @property
    def latest_version(self) -> str | None:
        """Return the newest published release, or the installed version when unknown."""

        data = self.coordinator.data or {}
        return data.get("latest_version") or self.installed_version

    @property
    def release_url(self) -> str | None:
        """Return the GitHub release page for the latest version."""

        data = self.coordinator.data or {}
        return data.get("release_url")

    @property
    def release_summary(self) -> str | None:
        """Return the release title reported by GitHub."""

        data = self.coordinator.data or {}
        return data.get("release_summary")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the VServer SSH Stats release-update entity for a config entry."""

    integration = await async_get_integration(hass, DOMAIN)
    coordinator = VServerReleaseCoordinator(hass)
    await coordinator.async_refresh()
    async_add_entities(
        [VServerIntegrationUpdateEntity(coordinator, entry, integration.version)]
    )
