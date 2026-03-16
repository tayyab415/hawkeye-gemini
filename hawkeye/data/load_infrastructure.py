"""Load Jakarta infrastructure from OSM Overpass into BigQuery."""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List

import requests
from google.cloud import bigquery

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
JAKARTA_LAT = -6.2
JAKARTA_LNG = 106.85
RADIUS_METERS = 20000

QUERIES = {
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


def _fetch_elements(query: str, retries: int = 4) -> List[dict]:
    headers = {"User-Agent": "hawkeye-step0-loader/1.0"}
    delay_seconds = 2

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                OVERPASS_URL,
                params={"data": query},
                headers=headers,
                timeout=90,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("elements", [])
        except Exception as exc:  # noqa: BLE001
            if attempt == retries:
                raise RuntimeError(
                    f"Overpass request failed after {retries} attempts"
                ) from exc
            time.sleep(delay_seconds)
            delay_seconds *= 2
    return []


def _build_rows() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []

    for infra_type, query in QUERIES.items():
        elements = _fetch_elements(query)
        for element in elements:
            tags = element.get("tags", {})
            rows.append(
                {
                    "id": f"{infra_type}:{element['id']}",
                    "name": tags.get("name", f"Unknown {infra_type}"),
                    "type": infra_type,
                    "latitude": element["lat"],
                    "longitude": element["lon"],
                    "capacity": str(tags.get("capacity", "")) or None,
                    "metadata": json.dumps(tags, ensure_ascii=True),
                }
            )
        # Respect Overpass public API rate limits.
        time.sleep(1.0)
    return rows


def _load_to_bigquery(project_id: str, rows: List[Dict[str, object]]) -> None:
    client = bigquery.Client(project=project_id)
    table_id = f"{project_id}.hawkeye.infrastructure_raw"

    schema = [
        bigquery.SchemaField("id", "STRING"),
        bigquery.SchemaField("name", "STRING"),
        bigquery.SchemaField("type", "STRING"),
        bigquery.SchemaField("latitude", "FLOAT64"),
        bigquery.SchemaField("longitude", "FLOAT64"),
        bigquery.SchemaField("capacity", "STRING"),
        bigquery.SchemaField("metadata", "STRING"),
    ]

    config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition="WRITE_TRUNCATE",
    )
    job = client.load_table_from_json(rows, table_id, job_config=config)
    job.result()


def main() -> None:
    project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("Set GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT before running.")

    rows = _build_rows()
    if not rows:
        raise RuntimeError("No infrastructure rows returned from Overpass API.")

    _load_to_bigquery(project_id=project_id, rows=rows)

    by_type: Dict[str, int] = {}
    for row in rows:
        key = str(row["type"])
        by_type[key] = by_type.get(key, 0) + 1

    print(f"Loaded {len(rows)} infrastructure records into hawkeye.infrastructure_raw")
    for infra_type in sorted(by_type):
        print(f"- {infra_type}: {by_type[infra_type]}")


if __name__ == "__main__":
    main()
