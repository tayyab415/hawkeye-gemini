#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GCP_PROJECT_ID:-}" ]]; then
  echo "GCP_PROJECT_ID is required"
  exit 1
fi

if [[ -z "${HAWKEYE_BUCKET:-}" ]]; then
  echo "HAWKEYE_BUCKET is required"
  exit 1
fi

SOURCE_FILE="${1:-/Users/tayyabkhan/Downloads/gemini-agent/groundsource.parquet}"

if [[ ! -f "${SOURCE_FILE}" ]]; then
  echo "Parquet file not found: ${SOURCE_FILE}"
  exit 1
fi

gsutil cp "${SOURCE_FILE}" "gs://${HAWKEYE_BUCKET}/data/groundsource.parquet"
bq --project_id="${GCP_PROJECT_ID}" load \
  --source_format=PARQUET \
  hawkeye.groundsource_raw \
  "gs://${HAWKEYE_BUCKET}/data/groundsource.parquet"

echo "Groundsource parquet loaded into hawkeye.groundsource_raw"
