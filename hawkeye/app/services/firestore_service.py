"""Firestore services — IncidentService + InfrastructureService."""

from __future__ import annotations

import json
import math
import time
from typing import Any

import requests
from google.cloud import firestore
from shapely.geometry import Point, shape


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
JAKARTA_LAT = -6.2
JAKARTA_LNG = 106.85
RADIUS_METERS = 20_000

_OVERPASS_QUERIES = {
    "hospital": (
        f'[out:json];node["amenity"="hospital"]'
        f"(around:{RADIUS_METERS},{JAKARTA_LAT},{JAKARTA_LNG});out body;"
    ),
    "school": (
        f'[out:json];node["amenity"="school"]'
        f"(around:{RADIUS_METERS},{JAKARTA_LAT},{JAKARTA_LNG});out body;"
    ),
    "shelter": (
        f'[out:json];(node["amenity"="shelter"]'
        f"(around:{RADIUS_METERS},{JAKARTA_LAT},{JAKARTA_LNG});"
        f'node["building"="civic"]'
        f"(around:{RADIUS_METERS},{JAKARTA_LAT},{JAKARTA_LNG}););out body;"
    ),
    "power_station": (
        f'[out:json];node["power"="substation"]'
        f"(around:{RADIUS_METERS},{JAKARTA_LAT},{JAKARTA_LNG});out body;"
    ),
    "water_treatment": (
        f'[out:json];node["man_made"="water_works"]'
        f"(around:{RADIUS_METERS},{JAKARTA_LAT},{JAKARTA_LNG});out body;"
    ),
}


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ======================================================================
# IncidentService
# ======================================================================

class IncidentService:
    def __init__(self, project_id: str):
        self.client = firestore.Client(project=project_id)
        self.project_id = project_id

    def log_event(
        self, event_type: str, severity: str, data: dict[str, Any]
    ) -> str:
        doc_ref = self.client.collection("incidents").document()
        doc_ref.set(
            {
                "event_type": event_type,
                "severity": severity,
                "data": data,
                "timestamp": firestore.SERVER_TIMESTAMP,
            }
        )
        return doc_ref.id

    def log_decision(
        self, decision_text: str, reasoning: str, confidence: float
    ) -> str:
        doc_ref = self.client.collection("decisions").document()
        doc_ref.set(
            {
                "decision": decision_text,
                "reasoning": reasoning,
                "confidence": confidence,
                "timestamp": firestore.SERVER_TIMESTAMP,
            }
        )
        return doc_ref.id

    def get_session_timeline(self) -> list[dict]:
        incidents = [
            {"collection": "incident", **doc.to_dict()}
            for doc in self.client.collection("incidents")
            .order_by("timestamp")
            .stream()
        ]
        decisions = [
            {"collection": "decision", **doc.to_dict()}
            for doc in self.client.collection("decisions")
            .order_by("timestamp")
            .stream()
        ]
        merged = incidents + decisions
        merged.sort(key=lambda d: d.get("timestamp") or 0)
        return merged

    def set_water_level(self, level_meters: float) -> None:
        self.client.collection("sensor_data").document("current").set(
            {
                "water_level_m": level_meters,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def get_water_level(self) -> dict:
        doc = self.client.collection("sensor_data").document("current").get()
        if doc.exists:
            return doc.to_dict()
        return {"water_level_m": 0.0, "updated_at": None}


# ======================================================================
# InfrastructureService
# ======================================================================

class InfrastructureService:
    def __init__(self, project_id: str):
        self.client = firestore.Client(project=project_id)
        self.project_id = project_id

    # ------------------------------------------------------------------
    # One-time loader: OSM Overpass → Firestore
    # ------------------------------------------------------------------
    def load_jakarta_infrastructure(self) -> int:
        """Download Jakarta infrastructure from OSM and write to Firestore.

        Returns the number of records written.
        """
        batch = self.client.batch()
        count = 0

        for infra_type, query in _OVERPASS_QUERIES.items():
            resp = requests.get(
                OVERPASS_URL,
                params={"data": query},
                headers={"User-Agent": "hawkeye-infra-loader/1.0"},
                timeout=90,
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])

            for el in elements:
                tags = el.get("tags", {})
                doc_id = f"{infra_type}:{el['id']}"
                doc_ref = self.client.collection("infrastructure").document(doc_id)
                batch.set(
                    doc_ref,
                    {
                        "name": tags.get("name", f"Unknown {infra_type}"),
                        "type": infra_type,
                        "latitude": el["lat"],
                        "longitude": el["lon"],
                        "capacity": tags.get("capacity"),
                        "metadata": json.dumps(tags, ensure_ascii=True),
                    },
                )
                count += 1

                if count % 400 == 0:
                    batch.commit()
                    batch = self.client.batch()

            time.sleep(1.0)

        batch.commit()
        return count

    # ------------------------------------------------------------------
    # Spatial queries (client-side filtering via Shapely)
    # ------------------------------------------------------------------
    def _get_by_type_in_polygon(
        self, infra_type: str, geojson: str | dict
    ) -> list[dict]:
        polygon = shape(
            json.loads(geojson) if isinstance(geojson, str) else geojson
        )
        docs = (
            self.client.collection("infrastructure")
            .where("type", "==", infra_type)
            .stream()
        )
        results = []
        for doc in docs:
            d = doc.to_dict()
            pt = Point(d["longitude"], d["latitude"])
            if polygon.contains(pt):
                d["id"] = doc.id
                results.append(d)
        return results

    def get_hospitals_in_polygon(self, geojson: str | dict) -> list[dict]:
        return self._get_by_type_in_polygon("hospital", geojson)

    def get_schools_in_polygon(self, geojson: str | dict) -> list[dict]:
        return self._get_by_type_in_polygon("school", geojson)

    def get_power_stations_in_polygon(self, geojson: str | dict) -> list[dict]:
        return self._get_by_type_in_polygon("power_station", geojson)

    def get_shelters_near(
        self, lat: float, lng: float, radius_km: float
    ) -> list[dict]:
        docs = (
            self.client.collection("infrastructure")
            .where("type", "==", "shelter")
            .stream()
        )
        results = []
        for doc in docs:
            d = doc.to_dict()
            dist = _haversine_km(lat, lng, d["latitude"], d["longitude"])
            if dist <= radius_km:
                d["id"] = doc.id
                d["distance_km"] = round(dist, 2)
                results.append(d)
        results.sort(key=lambda x: x["distance_km"])
        return results
