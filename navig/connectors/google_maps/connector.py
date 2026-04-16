"""
Google Maps Connector — Geocoding + Places API.

Authentication: API key via ``GOOGLE_MAPS_API_KEY`` env variable or navig vault.
No OAuth required.

Coverage:
    - Geocoding (forward + reverse)
    - Places Text Search
    - Places Nearby Search
    - Place Details

Usage:
    connector = GoogleMapsConnector()
    await connector.connect()
    results = await connector.search("coffee shops near Times Square")
    location = await connector.fetch("ChIJd8BlQ2BZwokRAFUEcm_qrcA")  # place_id
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from navig.connectors.base import BaseConnector, ConnectorManifest
from navig.connectors.errors import ConnectorAuthError, ConnectorRateLimitError
from navig.connectors.types import (
    Action,
    ActionResult,
    ConnectorDomain,
    ConnectorStatus,
    HealthStatus,
    Resource,
    ResourceType,
)

logger = logging.getLogger("navig.connectors.google_maps")

_GEOCODE_BASE = "https://maps.googleapis.com/maps/api/geocode/json"
_PLACES_TEXT_BASE = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_PLACES_NEARBY_BASE = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
_PLACE_DETAIL_BASE = "https://maps.googleapis.com/maps/api/place/details/json"


def _get(url: str, params: dict[str, str], timeout: int = 12) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}")
    resp = urllib.request.urlopen(req, timeout=timeout)
    import json

    return json.loads(resp.read())


class GoogleMapsConnector(BaseConnector):
    """Connector for Google Maps Platform — Geocoding and Places API.

    Auth: API key via ``GOOGLE_MAPS_API_KEY`` env variable or navig vault.
    No OAuth required.

    Supported keys registered in vault:
        ``google_maps/api_key``  – primary key for Maps + Places
        ``google_maps/geocoding_key`` – dedicated geocoding-only key (optional)
    """

    manifest = ConnectorManifest(
        id="google_maps",
        display_name="Google Maps",
        description=(
            "Geocoding (forward/reverse) and Places search via Google Maps Platform. "
            "Covers Maps Geocoding API and Places API (Text Search, Nearby, Details)."
        ),
        domain=ConnectorDomain.DATA,
        icon="🗺️",
        oauth_scopes=[],
        oauth_provider="",
        requires_oauth=False,
        can_search=True,
        can_fetch=True,
        can_act=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Load API key from env or vault label ``google_maps/api_key``."""
        key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        if not key:
            key = os.environ.get("GMAPS_API_KEY", "").strip()
        if not key:
            self._status = ConnectorStatus.ERROR
            raise ConnectorAuthError(
                self.manifest.id,
                "Google Maps API key not found. Set GOOGLE_MAPS_API_KEY or "
                "store in vault under 'google_maps/api_key'.",
            )
        self._api_key = key
        self._status = ConnectorStatus.CONNECTED
        logger.debug("GoogleMaps connector connected.")

    async def disconnect(self) -> None:
        self._api_key = None
        self._status = ConnectorStatus.DISCONNECTED

    # ── Search: text search via Places Text Search or Geocode ───────────────

    async def search(
        self,
        query: str,
        limit: int = 10,
        **kwargs: Any,
    ) -> list[Resource]:
        """Search for places matching *query*.

        Falls back to Geocoding if query looks like an address (no results
        from Places Text Search).

        Args:
            query: Natural-language place or address query.
            limit: Max results (1–20).
            kwargs:
                location (str): lat,lng bias centre, e.g. "40.7128,-74.0060".
                radius (int): Bias radius in metres (default 5000).
                mode (str): "places" | "geocode" (default "places").
        """
        self._require_connected()
        mode = kwargs.get("mode", "places")
        if mode == "geocode":
            return await self._geocode_search(query, limit)
        return await self._places_text_search(query, limit, kwargs)

    async def _places_text_search(self, query: str, limit: int, kwargs: dict) -> list[Resource]:
        params: dict[str, str] = {"query": query, "key": self._api_key}
        if "location" in kwargs:
            params["location"] = str(kwargs["location"])
            params["radius"] = str(kwargs.get("radius", 5000))
        data = _get(_PLACES_TEXT_BASE, params)
        status = data.get("status", "")
        if status == "REQUEST_DENIED":
            raise ConnectorAuthError(self.manifest.id, data.get("error_message", "REQUEST_DENIED"))
        if status == "OVER_QUERY_LIMIT":
            raise ConnectorRateLimitError(self.manifest.id, "Google Maps quota exceeded")
        results: list[Resource] = []
        for place in data.get("results", [])[:limit]:
            loc = place.get("geometry", {}).get("location", {})
            results.append(
                Resource(
                    id=place.get("place_id", ""),
                    title=place.get("name", ""),
                    body=(
                        f"{place.get('formatted_address', '')}\n"
                        f"Rating: {place.get('rating', 'N/A')} "
                        f"({place.get('user_ratings_total', 0)} reviews)\n"
                        f"Types: {', '.join(place.get('types', []))}"
                    ),
                    url=(
                        f"https://www.google.com/maps/search/?api=1"
                        f"&query={urllib.parse.quote(place.get('name', ''))}"
                        f"&query_place_id={place.get('place_id', '')}"
                    ),
                    resource_type=ResourceType.DOCUMENT,
                    metadata={
                        "place_id": place.get("place_id"),
                        "lat": loc.get("lat"),
                        "lng": loc.get("lng"),
                        "rating": place.get("rating"),
                        "types": place.get("types", []),
                        "open_now": place.get("opening_hours", {}).get("open_now"),
                    },
                )
            )
        return results

    async def _geocode_search(self, address: str, limit: int) -> list[Resource]:
        data = _get(_GEOCODE_BASE, {"address": address, "key": self._api_key})
        status = data.get("status", "")
        if status == "REQUEST_DENIED":
            raise ConnectorAuthError(self.manifest.id, data.get("error_message", "REQUEST_DENIED"))
        results: list[Resource] = []
        for item in data.get("results", [])[:limit]:
            loc = item.get("geometry", {}).get("location", {})
            results.append(
                Resource(
                    id=item.get("place_id", ""),
                    title=item.get("formatted_address", ""),
                    body=(
                        f"Lat: {loc.get('lat')}, Lng: {loc.get('lng')}\n"
                        f"Types: {', '.join(item.get('types', []))}"
                    ),
                    url=(
                        f"https://www.google.com/maps/search/?api=1"
                        f"&query={loc.get('lat')},{loc.get('lng')}"
                    ),
                    resource_type=ResourceType.DOCUMENT,
                    metadata={
                        "place_id": item.get("place_id"),
                        "lat": loc.get("lat"),
                        "lng": loc.get("lng"),
                        "location_type": item.get("geometry", {}).get("location_type"),
                        "types": item.get("types", []),
                    },
                )
            )
        return results

    # ── Fetch: Place Details by place_id ────────────────────────────────────

    async def fetch(self, place_id: str, **kwargs: Any) -> Resource | None:
        """Fetch detailed information for a Google Maps place_id.

        Args:
            place_id: Google Maps place_id string (e.g. "ChIJd8BlQ2BZwokRAFUEcm_qrcA").
        """
        self._require_connected()
        fields = kwargs.get(
            "fields",
            "name,formatted_address,geometry,rating,user_ratings_total,"
            "formatted_phone_number,website,opening_hours,types,url",
        )
        data = _get(
            _PLACE_DETAIL_BASE,
            {"place_id": place_id, "fields": fields, "key": self._api_key},
        )
        status = data.get("status", "")
        if status == "REQUEST_DENIED":
            raise ConnectorAuthError(self.manifest.id, data.get("error_message", "REQUEST_DENIED"))
        if status == "NOT_FOUND":
            return None
        result = data.get("result", {})
        if not result:
            return None
        loc = result.get("geometry", {}).get("location", {})
        return Resource(
            id=place_id,
            title=result.get("name", ""),
            body=(
                f"{result.get('formatted_address', '')}\n"
                f"Phone: {result.get('formatted_phone_number', 'N/A')}\n"
                f"Website: {result.get('website', 'N/A')}\n"
                f"Rating: {result.get('rating', 'N/A')} "
                f"({result.get('user_ratings_total', 0)} reviews)"
            ),
            url=result.get("url", ""),
            resource_type=ResourceType.DOCUMENT,
            metadata={
                "place_id": place_id,
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
                "phone": result.get("formatted_phone_number"),
                "website": result.get("website"),
                "rating": result.get("rating"),
                "types": result.get("types", []),
                "open_now": result.get("opening_hours", {}).get("open_now"),
                "hours": result.get("opening_hours", {}).get("weekday_text", []),
            },
        )

    # ── Act: reverse geocode ─────────────────────────────────────────────────

    async def act(self, action: Action) -> ActionResult:
        """Supported actions:

        reverse_geocode: {"lat": float, "lng": float} → address string
        """
        self._require_connected()
        if action.name == "reverse_geocode":
            lat = action.params.get("lat")
            lng = action.params.get("lng")
            if lat is None or lng is None:
                return ActionResult(success=False, error="lat and lng required")
            data = _get(_GEOCODE_BASE, {"latlng": f"{lat},{lng}", "key": self._api_key})
            results = data.get("results", [])
            address = results[0].get("formatted_address", "") if results else ""
            return ActionResult(success=bool(address), data={"address": address})
        if action.name == "nearby_search":
            params_data = action.params
            params: dict[str, str] = {
                "location": f"{params_data['lat']},{params_data['lng']}",
                "radius": str(params_data.get("radius", 1000)),
                "key": self._api_key,
            }
            if "type" in params_data:
                params["type"] = str(params_data["type"])
            data = _get(_PLACES_NEARBY_BASE, params)
            return ActionResult(
                success=True,
                data={"results": data.get("results", [])[: params_data.get("limit", 10)]},
            )
        return ActionResult(success=False, error=f"Unknown action: {action.name}")

    # ── Health ───────────────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """Ping geocoding API with a minimal request."""
        if not self._api_key:
            return HealthStatus(healthy=False, message="Not connected", latency_ms=0)
        t0 = time.monotonic()
        try:
            data = _get(
                _GEOCODE_BASE,
                {"address": "New York", "key": self._api_key, "result_type": "locality"},
                timeout=8,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            status = data.get("status", "")
            if status in ("OK", "ZERO_RESULTS"):
                return HealthStatus(
                    healthy=True, message=f"Maps API OK ({status})", latency_ms=latency_ms
                )
            if status == "REQUEST_DENIED":
                return HealthStatus(
                    healthy=False,
                    message=f"REQUEST_DENIED: {data.get('error_message', '')}",
                    latency_ms=latency_ms,
                )
            return HealthStatus(
                healthy=False, message=f"Unexpected status: {status}", latency_ms=latency_ms
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - t0) * 1000)
            return HealthStatus(healthy=False, message=str(exc), latency_ms=latency_ms)
