#!/usr/bin/env bash
set -euo pipefail

echo "Deploying HawkEye to Google Cloud Run..."

PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
SERVICE_NAME="hawkeye-gemini"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Build the container image using Cloud Build
echo "Building the Docker image via Google Cloud Build..."
gcloud builds submit --tag ${IMAGE_NAME} .

# Deploy the image to Cloud Run
echo "Deploying the image to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
  --image ${IMAGE_NAME} \
  --region ${REGION} \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID}" \
  --memory 2Gi \
  --cpu 2 \
  --port 8080

echo "Deployment completed successfully!"
