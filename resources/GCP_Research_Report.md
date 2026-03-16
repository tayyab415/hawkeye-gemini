# AEGIS Project: Comprehensive GCP Research Report
## Predictive Digital Risk Twin for Disaster Response & Infrastructure Surveillance

**Project Budget:** $4,000 GCP Credits  
**Timeline:** 10-Day Build Sprint  
**Research Date:** March 2025

---

## Executive Summary

This report provides comprehensive technical research on Google Cloud Platform (GCP) services for the AEGIS surveillance and disaster response project. AEGIS requires a scalable, cost-effective infrastructure leveraging AI/ML for predictive risk analysis, real-time monitoring, and automated response systems.

### Key Findings:
- **Vertex AI** provides a complete ML platform with Gemini models, custom training, and agent builder capabilities
- **Cloud Run with NVIDIA L4 GPUs** enables serverless ML inference for SAM 3D and other vision models
- **Firestore** offers real-time database capabilities perfect for incident tracking
- **Cloud Storage** with lifecycle policies optimizes media storage costs
- **$4,000 credits** can support significant development and initial deployment

---

## 1. VERTEX AI PLATFORM CAPABILITIES

### 1.1 Core Features & Services

#### Model Garden
Centralized hub with enterprise-ready models:
- **Google Models:** Gemini (multimodal), Imagen (images), Veo (video), Codey (code)
- **Partner Models:** Anthropic Claude, Meta Llama, Mistral AI, DeepSeek
- **Open-Source Options:** Fully customizable foundation models
- **Managed APIs:** Pre-configured endpoints with automatic scaling

#### AutoML (No-Code ML)
Automated model creation for:
- **Tabular Data:** Predictions from structured datasets
- **Images:** Object detection, classification, segmentation
- **Text:** Sentiment analysis, entity extraction, classification
- **Video:** Action recognition, object tracking

**Pricing:** $3.465 per node hour for training  
**Performance:** 60% faster training since 2024 (3-6 hours typical)

#### Vertex AI Studio
Generative AI experimentation environment:
- Prompt design with real-time feedback
- Multimodal testing (text, images, video)
- Model comparison and A/B testing
- Fine-tuning with proprietary data
- Version control for prompts

#### Custom Training
Maximum flexibility with popular frameworks:
- **Supported Frameworks:** TensorFlow, PyTorch, XGBoost, scikit-learn
- **Hardware Options:** NVIDIA GPUs (A100, H100, L40S), TPU v5p clusters
- **Distributed Training:** Handle trillion-parameter models
- **Spot VMs:** Save up to 70% on training costs

**Performance:** TPU v5p delivers 2.8x faster training than v4 pods

### 1.2 Vertex AI Agent Builder (Released Late 2024)

Production-scale AI agent creation:
- **Pre-built Templates:** RAG (Retrieval-Augmented Generation) patterns
- **Tool Integration:** Connect to APIs, databases, enterprise systems
- **LangChain/LlamaIndex Support:** Popular orchestration frameworks
- **MCP Server Integration:** Access Google services (BigQuery, Maps)
- **Cloud API Registry:** Centralized tool governance

**Pricing:** $0.00994 per vCPU-hour, $0.0105 per GiB-hour

### 1.3 BigQuery ML Integration

Direct BigQuery integration for SQL-based ML:
- Build models using SQL queries
- One-click deployment to REST endpoints
- No data migration required
- Train on massive datasets in-place

### 1.4 Custom Container Deployment

For frameworks not covered by pre-built containers:

**Container Requirements:**
```dockerfile
# Required endpoints
ENV AIP_HTTP_PORT=8080
ENV AIP_HEALTH_ROUTE=/health
ENV AIP_PREDICT_ROUTE=/predict

# Expose port
EXPOSE 8080
```

**Python Example (Flask):**
```python
from flask import Flask, request, jsonify
import os

app = Flask(__name__)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200

@app.route("/predict", methods=["POST"])
def predict():
    request_json = request.get_json()
    instances = request_json.get("instances", [])
    # Process predictions
    return jsonify({"predictions": results}), 200

if __name__ == "__main__":
    port = int(os.environ.get("AIP_HTTP_PORT", 8080))
    app.run(host="0.0.0.0", port=port)
```

**Deployment Commands:**
```bash
# Upload model to Vertex AI
MODEL_NAME="aegis-risk-model"
ENDPOINT_NAME="aegis-predictions"

# Create endpoint
ENDPOINT_ID=$(gcloud ai endpoints create \
  --display-name=$ENDPOINT_NAME \
  --region=us-central1 \
  --format="value(name)")

# Deploy model
gcloud ai endpoints deploy-model $ENDPOINT_ID \
  --model=$MODEL_NAME \
  --display-name=aegis-deployment \
  --machine-type=n1-standard-4 \
  --min-replica-count=1 \
  --max-replica-count=3
```

---

## 2. CLOUD RUN FOR CONTAINERIZED DEPLOYMENTS

### 2.1 Overview

Cloud Run is a fully-managed serverless platform for containerized applications:
- **Services:** Continuous HTTP request handlers
- **Jobs:** Batch processing that exits when complete
- **Automatic Scaling:** From zero to thousands of instances
- **Pay-per-use:** Only pay when processing requests

### 2.2 GPU Support (NVIDIA L4)

**Key Capabilities:**
- Serverless ML inference with NVIDIA L4 GPUs
- Real-time AI inference for vision models
- Video processing and 3D rendering
- Supports Gemma, Llama, and custom models

**GPU Pricing (Tier 1 Regions):**
| Configuration | Price per Second | Price per Hour |
|--------------|------------------|----------------|
| NVIDIA L4 (No zonal redundancy) | $0.0001867 | ~$0.67 |
| NVIDIA L4 (Zonal redundancy) | $0.0002909 | ~$1.05 |
| NVIDIA RTX Pro 6000 | $0.00036522 | ~$1.31 |

**Deployment with GPU:**
```bash
# Deploy with GPU support
gcloud beta run deploy aegis-sam3d \
  --image=gcr.io/PROJECT_ID/aegis-sam3d:latest \
  --region=us-central1 \
  --platform=managed \
  --gpu=1 \
  --gpu-type=nvidia-l4 \
  --memory=16Gi \
  --cpu=4 \
  --no-cpu-throttling \
  --max-instances=1 \
  --execution-environment=gen2 \
  --timeout=600s \
  --allow-unauthenticated
```

**Important GPU Requirements:**
- Minimum 16GB memory for GPU-enabled services
- Requires `--no-cpu-throttling` flag
- `--execution-environment=gen2` required
- Max instances must be 7 or lower (preview limitation)
- Only `us-central1` currently supports GPUs

### 2.3 Standard Cloud Run Pricing

| Resource | Price (per unit) |
|----------|------------------|
| CPU (per vCPU-second) | $0.000018 |
| Memory (per GiB-second) | $0.000002 |
| Requests (per million) | $0.40 |
| Networking (per GB) | $0.12 |

### 2.4 Free Tier (Always Free)

| Resource | Free Quota |
|----------|------------|
| Requests | 2 million per month |
| CPU | 180,000 vCPU-seconds per month |
| Memory | 360,000 GiB-seconds per month |
| Networking | 1 GB outbound (North America) |

### 2.5 Cloud Run Functions (Formerly Cloud Functions)

Merged into Cloud Run in August 2024:
- Built with Cloud Build
- Deployed as Cloud Run services
- Access to all Cloud Run features
- Event-driven triggers (Pub/Sub, Firestore, Storage)

---

## 3. FIRESTORE FOR REAL-TIME INCIDENT DATABASE

### 3.1 Overview

Firestore is a serverless NoSQL document database:
- **Real-time sync:** Live data updates across clients
- **Offline persistence:** Works without connectivity
- **Automatic scaling:** Handles millions of concurrent connections
- **Strong consistency:** ACID transactions

### 3.2 Data Model for AEGIS

```javascript
// Incident Collection Structure
incidents/
  {incident_id}/
    - timestamp: timestamp
    - location: geopoint
    - severity: string (critical|high|medium|low)
    - type: string (fire|flood|structural|surveillance)
    - status: string (active|contained|resolved)
    - sensor_data: map
    - ai_analysis: map
    - media_urls: array

// Sensors Collection
sensors/
  {sensor_id}/
    - location: geopoint
    - type: string (camera|thermal|motion)
    - status: string (online|offline|maintenance)
    - last_reading: map
    - firmware_version: string

// Alerts Collection
alerts/
  {alert_id}/
    - incident_id: reference
    - recipients: array
    - channels: array (sms|email|push)
    - sent_at: timestamp
    - status: string (pending|sent|failed)
```

### 3.3 Real-time Listeners (Python)

```python
from google.cloud import firestore

db = firestore.Client()

# Listen for new critical incidents
def on_incident_change(doc_snapshot, changes, read_time):
    for change in changes:
        if change.type.name == 'ADDED':
            incident = change.document.to_dict()
            if incident.get('severity') == 'critical':
                trigger_alert(incident)

# Watch critical incidents query
query = db.collection('incidents').where('severity', '==', 'critical')
query_watch = query.on_snapshot(on_incident_change)
```

### 3.4 Firestore Pricing

| Operation | Free Tier/Day | Price (beyond free) |
|-----------|---------------|---------------------|
| Document Reads | 50,000 | $0.03 per 100,000 |
| Document Writes | 20,000 | $0.09 per 100,000 |
| Document Deletes | 20,000 | $0.01 per 100,000 |
| Stored Data | 1 GiB | $0.000205 per GiB/day |
| Outbound Transfer | 10 GiB/month | Varies by region |

**Free Tier Summary:**
- 1 GiB storage
- 50,000 reads/day
- 20,000 writes/day
- 20,000 deletes/day
- 10 GiB outbound/month

### 3.5 Best Practices for Surveillance Systems

1. **Sharding Strategy:** Split high-volume collections by time periods
2. **Compound Indexes:** Pre-define indexes for common queries
3. **TTL Policies:** Auto-delete old incident data
4. **Batch Operations:** Use batched writes for bulk updates
5. **Connection Pooling:** Reuse Firestore clients across requests

---

## 4. CLOUD STORAGE FOR MEDIA & ASSETS

### 4.1 Storage Classes

| Class | Best For | Min Duration | Storage Cost | Retrieval |
|-------|----------|--------------|--------------|-----------|
| **Standard** | Hot data, active apps | None | $0.020/GB | Free |
| **Nearline** | Monthly access | 30 days | $0.010/GB | $0.01/GB |
| **Coldline** | Quarterly access | 90 days | $0.004/GB | $0.02/GB |
| **Archive** | Yearly access | 365 days | $0.0012/GB | $0.05/GB |

### 4.2 AEGIS Storage Strategy

```bash
# Create buckets for different data types

# Active incident media (Standard)
gcloud storage buckets create gs://aegis-incidents-active \
  --location=us-central1 \
  --default-storage-class=STANDARD

# Processed surveillance footage (Nearline)
gcloud storage buckets create gs://aegis-surveillance-archive \
  --location=us-central1 \
  --default-storage-class=NEARLINE

# Long-term compliance records (Coldline)
gcloud storage buckets create gs://aegis-compliance-records \
  --location=us-central1 \
  --default-storage-class=COLDLINE

# Historical disaster data (Archive)
gcloud storage buckets create gs://aegis-historical-data \
  --location=us-central1 \
  --default-storage-class=ARCHIVE
```

### 4.3 Lifecycle Policies

```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
        "condition": {
          "age": 30,
          "matchesPrefix": ["incidents/"]
        }
      },
      {
        "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
        "condition": {
          "age": 90,
          "matchesPrefix": ["incidents/"]
        }
      },
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": 2555,
          "matchesPrefix": ["temp/"]
        }
      }
    ]
  }
}
```

Apply lifecycle policy:
```bash
gcloud storage buckets update gs://aegis-incidents-active \
  --lifecycle-file=lifecycle.json
```

### 4.4 Signed URLs for Secure Access

```python
from google.cloud import storage
from datetime import timedelta

storage_client = storage.Client()
bucket = storage_client.bucket('aegis-incidents-active')
blob = bucket.blob('incidents/2025-03-15/fire-damage.jpg')

# Generate signed URL valid for 1 hour
url = blob.generate_signed_url(
    version="v4",
    expiration=timedelta(hours=1),
    method="GET"
)
```

### 4.5 Free Tier

| Resource | Free Quota |
|----------|------------|
| Standard Storage | 5 GB/month |
| Operations | 5,000 Class A, 50,000 Class B |
| Network Egress | 1 GB/month (North America) |

---

## 5. CLOUD BUILD FOR CI/CD

### 5.1 Basic Pipeline Configuration

```yaml
# cloudbuild.yaml
steps:
  # Step 1: Install dependencies and run tests
  - name: 'python:3.11-slim'
    entrypoint: 'pip'
    args: ['install', '-r', 'requirements.txt', '-t', 'lib/']
  
  - name: 'python:3.11-slim'
    entrypoint: 'python'
    args: ['-m', 'pytest', 'tests/']

  # Step 2: Build Docker image
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-t'
      - 'us-central1-docker.pkg.dev/$PROJECT_ID/aegis-repo/aegis-api:$COMMIT_SHA'
      - '-t'
      - 'us-central1-docker.pkg.dev/$PROJECT_ID/aegis-repo/aegis-api:latest'
      - '.'

  # Step 3: Push to Artifact Registry
  - name: 'gcr.io/cloud-builders/docker'
    args: ['push', 'us-central1-docker.pkg.dev/$PROJECT_ID/aegis-repo/aegis-api:$COMMIT_SHA']

  # Step 4: Deploy to Cloud Run
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: gcloud
    args:
      - 'run'
      - 'deploy'
      - 'aegis-api'
      - '--image'
      - 'us-central1-docker.pkg.dev/$PROJECT_ID/aegis-repo/aegis-api:$COMMIT_SHA'
      - '--region'
      - 'us-central1'
      - '--platform'
      - 'managed'
      - '--allow-unauthenticated'
      - '--memory'
      - '1Gi'
      - '--cpu'
      - '1'
      - '--max-instances'
      - '10'
      - '--set-env-vars'
      - 'ENV=production'

# Substitution variables
substitutions:
  _SERVICE_NAME: 'aegis-api'
  _REGION: 'us-central1'

# Options
options:
  logging: CLOUD_LOGGING_ONLY
  machineType: 'E2_HIGHCPU_8'

# Timeout
timeout: '1200s'

# Store images
images:
  - 'us-central1-docker.pkg.dev/$PROJECT_ID/aegis-repo/aegis-api:$COMMIT_SHA'
  - 'us-central1-docker.pkg.dev/$PROJECT_ID/aegis-repo/aegis-api:latest'
```

### 5.2 Create Build Trigger

```bash
# GitHub integration
gcloud builds triggers create github \
  --name=aegis-api-trigger \
  --repo-owner=YOUR_ORG \
  --repo-name=aegis-api \
  --branch-pattern='^main$' \
  --build-config=cloudbuild.yaml

# Cloud Source Repositories
gcloud builds triggers create cloud-source-repositories \
  --name=aegis-api-trigger \
  --repo=aegis-api \
  --branch-pattern='^main$' \
  --build-config=cloudbuild.yaml
```

### 5.3 Free Tier

| Resource | Free Quota |
|----------|------------|
| Build Minutes | 120 minutes/day |

---

## 6. IAM & SERVICE ACCOUNTS

### 6.1 Best Practices

1. **Principle of Least Privilege:** Grant minimum necessary permissions
2. **Dedicated Service Accounts:** One per application/component
3. **Avoid Service Account Keys:** Use Workload Identity when possible
4. **Regular Audits:** Review permissions quarterly
5. **Short-lived Credentials:** Rotate keys regularly

### 6.2 AEGIS Service Account Structure

```bash
# Create service accounts
gcloud iam service-accounts create aegis-api-sa \
  --display-name="AEGIS API Service Account"

gcloud iam service-accounts create aegis-ml-sa \
  --display-name="AEGIS ML Service Account"

gcloud iam service-accounts create aegis-pipeline-sa \
  --display-name="AEGIS CI/CD Pipeline Service Account"
```

### 6.3 Required IAM Roles

```bash
# AEGIS API Service Account
export API_SA="aegis-api-sa@PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$API_SA" \
  --role="roles/datastore.user"  # Firestore access

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$API_SA" \
  --role="roles/storage.objectViewer"  # Cloud Storage read

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$API_SA" \
  --role="roles/logging.logWriter"  # Cloud Logging

# AEGIS ML Service Account
export ML_SA="aegis-ml-sa@PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$ML_SA" \
  --role="roles/aiplatform.user"  # Vertex AI access

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$ML_SA" \
  --role="roles/storage.objectAdmin"  # Cloud Storage full access

# Cloud Build Service Account
export CB_SA="PROJECT_NUMBER@cloudbuild.gserviceaccount.com"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$CB_SA" \
  --role="roles/run.admin"  # Cloud Run deployment

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:$CB_SA" \
  --role="roles/iam.serviceAccountUser"  # Act as service accounts
```

### 6.4 Workload Identity (GKE)

```yaml
# Enable Workload Identity on GKE cluster
gcloud container clusters create aegis-cluster \
  --workload-pool=PROJECT_ID.svc.id.goog \
  --region=us-central1

# Create Kubernetes service account
kubectl create serviceaccount aegis-ksa \
  --namespace=default

# Bind to Google service account
gcloud iam service-accounts add-iam-policy-binding \
  aegis-api-sa@PROJECT_ID.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:PROJECT_ID.svc.id.goog[default/aegis-ksa]"

# Annotate Kubernetes service account
kubectl annotate serviceaccount aegis-ksa \
  --namespace=default \
  iam.gke.io/gcp-service-account=aegis-api-sa@PROJECT_ID.iam.gserviceaccount.com
```

---

## 7. APIs TO ENABLE

### 7.1 Core APIs for AEGIS

```bash
# Enable all required APIs
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  monitoring.googleapis.com \
  logging.googleapis.com \
  cloudtrace.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  pubsub.googleapis.com \
  eventarc.googleapis.com \
  cloudfunctions.googleapis.com \
  iamcredentials.googleapis.com \
  compute.googleapis.com
```

### 7.2 API-by-API Breakdown

| API | Purpose | Required For |
|-----|---------|--------------|
| `run.googleapis.com` | Cloud Run deployment | Container services |
| `firestore.googleapis.com` | Real-time database | Incident tracking |
| `storage.googleapis.com` | Object storage | Media/assets |
| `cloudbuild.googleapis.com` | CI/CD pipeline | Build automation |
| `aiplatform.googleapis.com` | Vertex AI | ML models |
| `monitoring.googleapis.com` | Cloud Monitoring | Observability |
| `logging.googleapis.com` | Cloud Logging | Log aggregation |
| `artifactregistry.googleapis.com` | Container registry | Image storage |
| `secretmanager.googleapis.com` | Secrets management | Credentials |
| `pubsub.googleapis.com` | Event streaming | Async messaging |
| `eventarc.googleapis.com` | Event routing | Trigger automation |

### 7.3 Verify Enabled APIs

```bash
# List enabled APIs
gcloud services list

# List available APIs
gcloud services list --available

# Check specific API
gcloud services describe firestore.googleapis.com
```

---

## 8. PRICING & COST OPTIMIZATION

### 8.1 Cost Estimation for AEGIS

**Monthly Cost Estimate (Moderate Usage):**

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| Cloud Run (CPU/Memory) | 100K requests/day | ~$50-100 |
| Cloud Run GPU (L4) | 2 hrs/day inference | ~$40-60 |
| Firestore | 500K reads, 100K writes | ~$20-30 |
| Cloud Storage | 100 GB Standard | ~$2-3 |
| Vertex AI Predictions | 10K predictions | ~$10-20 |
| Cloud Build | 50 builds | ~$5-10 |
| **Total Estimated** | | **~$127-223/month** |

### 8.2 Cost Optimization Strategies

#### 1. Use Spot VMs for Training
```bash
# Save up to 70% on training costs
gcloud ai custom-jobs create \
  --region=us-central1 \
  --display-name=aegis-training \
  --worker-pool-spec=machine-type=n1-standard-4,accelerator-type=NVIDIA_TESLA_T4,accelerator-count=1,replica-count=1,spot=true \
  --python-package-uris=gs://aegis-bucket/trainer-0.1.tar.gz \
  --python-module=trainer.task
```

#### 2. Cloud Storage Lifecycle Policies
- Move data to cheaper tiers after 30/90 days
- Delete temporary files automatically
- Use Archive for compliance data

#### 3. Cloud Run Optimization
```bash
# Scale to zero when idle
gcloud run services update aegis-api \
  --min-instances=0 \
  --max-instances=10

# Use smaller instance sizes for low-traffic
gcloud run services update aegis-api \
  --memory=256Mi \
  --cpu=1
```

#### 4. Firestore Optimization
- Use batched writes for bulk operations
- Implement local caching
- Use compound indexes efficiently

#### 5. Committed Use Discounts (CUDs)
- 1-year commitment: ~20% discount
- 3-year commitment: ~35% discount
- Flexible CUDs apply across services

### 8.3 Budget Alerts

```bash
# Create budget alert
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name="AEGIS Budget Alert" \
  --budget-amount=300USD \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=80 \
  --threshold-rule=percent=100
```

---

## 9. FREE TIER SUMMARY

### 9.1 Always Free Resources

| Service | Free Quota |
|---------|------------|
| **Compute Engine** | 1 e2-micro instance (US regions) |
| **Cloud Storage** | 5 GB Standard storage |
| **Cloud Run** | 2M requests, 180K vCPU-sec, 360K GiB-sec |
| **Firestore** | 1 GiB storage, 50K reads/day, 20K writes/day |
| **Cloud Build** | 120 build minutes/day |
| **BigQuery** | 1 TB queries/month |
| **Pub/Sub** | 10 GB messages/month |
| **Cloud Functions** | 2M invocations/month |

### 9.2 $300 Trial Credit

New customers receive:
- $300 credit for 90 days
- Valid for all GCP services
- No auto-charge after expiration

### 9.3 Maximizing Free Tier

1. Use `us-central1`, `us-west1`, or `us-east1` for Compute Engine free tier
2. Stay within Firestore daily quotas
3. Use Cloud Run for serverless (scales to zero)
4. Archive old data to reduce storage costs
5. Monitor usage with Cloud Billing Reports

---

## 10. AI AGENT DEPLOYMENT PATTERNS

### 10.1 Microservices Architecture

```
                    ┌─────────────────┐
                    │   API Gateway   │
                    │   (Cloud Run)   │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  Incident     │   │  ML Inference │   │  Notification │
│  Service      │   │  Service      │   │  Service      │
│  (Cloud Run)  │   │  (Cloud Run   │   │  (Cloud Run)  │
│               │   │   + GPU)      │   │               │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                    │                    │
        │            ┌───────┴───────┐            │
        │            │               │            │
        ▼            ▼               ▼            ▼
┌───────────────┐   ┌──────────┐  ┌──────────┐  ┌──────────┐
│  Firestore    │   │  Vertex  │  │  Cloud   │  │  Pub/Sub │
│  (Real-time)  │   │  AI      │  │  Storage │  │  (Events)│
└───────────────┘   └──────────┘  └──────────┘  └──────────┘
```

### 10.2 Event-Driven Pattern

```python
# Cloud Run function triggered by Firestore
def on_incident_created(event, context):
    """Triggered when new incident is created"""
    incident_data = event['value']['fields']
    
    # Run ML analysis
    if incident_data['severity']['stringValue'] == 'critical':
        # Trigger immediate analysis
        analyze_with_vertex_ai(incident_data)
        
    # Send notifications
    send_alerts(incident_data)
    
    # Archive media
    archive_incident_media(incident_data)
```

### 10.3 SAM 3D Deployment on Cloud Run GPU

```dockerfile
# Dockerfile for SAM 3D inference
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

WORKDIR /app

# Install Python and dependencies
RUN apt-get update && apt-get install -y python3 python3-pip

# Install SAM and dependencies
COPY requirements.txt .
RUN pip3 install -r requirements.txt

# Download SAM checkpoint
RUN python3 -c "from segment_anything import sam_model_registry; \
    sam_model_registry['vit_h'](checkpoint='sam_vit_h.pth')"

# Copy application code
COPY app.py .

# Expose port
EXPOSE 8080

# Run server
CMD ["python3", "app.py"]
```

```python
# app.py - SAM 3D inference server
from flask import Flask, request, jsonify
from segment_anything import sam_model_registry, SamPredictor
import torch
import numpy as np
import os
import base64
from io import BytesIO
from PIL import Image

app = Flask(__name__)

# Load SAM model
print("Loading SAM model...")
device = "cuda" if torch.cuda.is_available() else "cpu"
sam = sam_model_registry["vit_h"](checkpoint="sam_vit_h.pth")
sam.to(device=device)
predictor = SamPredictor(sam)
print(f"SAM model loaded on {device}")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "device": device}), 200

@app.route("/predict", methods=["POST"])
def predict():
    try:
        data = request.get_json()
        
        # Decode base64 image
        image_data = base64.b64decode(data['image'])
        image = Image.open(BytesIO(image_data))
        image_np = np.array(image)
        
        # Set image for predictor
        predictor.set_image(image_np)
        
        # Get point prompts
        point_coords = np.array(data.get('point_coords', [[0.5, 0.5]]))
        point_labels = np.array(data.get('point_labels', [1]))
        
        # Run prediction
        masks, scores, logits = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=True
        )
        
        # Return results
        return jsonify({
            "masks": masks.tolist(),
            "scores": scores.tolist(),
            "device": device
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
```

### 10.4 RAG Pattern for Disaster Response

```python
# Vertex AI Agent Builder RAG implementation
from vertexai.preview import rag
from vertexai.preview.generative_models import GenerativeModel

# Create RAG corpus
rag_corpus = rag.create_corpus(
    display_name="aegis-disaster-knowledge",
    description="Knowledge base for disaster response"
)

# Import documents
rag.import_files(
    corpus_name=rag_corpus.name,
    paths=["gs://aegis-knowledge-base/"],
    chunk_size=512,
    chunk_overlap=50
)

# Create RAG retriever
retriever = rag.Retriever(
    corpus_name=rag_corpus.name,
    similarity_top_k=5
)

# Use with Gemini
rag_model = GenerativeModel(
    model_name="gemini-1.5-pro",
    tools=[retriever]
)

# Query for disaster response
response = rag_model.generate_content(
    "What are the evacuation procedures for a Category 4 hurricane?"
)
```

---

## 11. MONITORING & LOGGING (CLOUD OPERATIONS)

### 11.1 Cloud Monitoring Setup

```bash
# Create custom dashboard
gcloud monitoring dashboards create \
  --config-from-file=dashboard.json
```

```json
{
  "displayName": "AEGIS Monitoring Dashboard",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "Cloud Run Requests",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\""
              }
            }
          }]
        }
      },
      {
        "title": "Firestore Reads",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"firestore.googleapis.com/Database\" AND metric.type=\"firestore.googleapis.com/document/read_count\""
              }
            }
          }]
        }
      }
    ]
  }
}
```

### 11.2 Alerting Policies

```bash
# Create alert for high error rate
gcloud alpha monitoring policies create \
  --policy="displayName='AEGIS High Error Rate',
  conditions=[{
    displayName='Error rate > 5%',
    conditionThreshold={
      filter='resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class!=\"2xx\"',
      aggregations=[{alignmentPeriod=300s,perSeriesAligner=ALIGN_RATE}],
      comparison=COMPARISON_GT,
      thresholdValue=0.05,
      duration=300s
    }
  }],
  alertStrategy=notificationRateLimit={period=300s},
  notificationChannels=['projects/PROJECT_ID/notificationChannels/CHANNEL_ID']"
```

### 11.3 Structured Logging (Python)

```python
import logging
import json
from google.cloud.logging import Client
from google.cloud.logging.handlers import CloudLoggingHandler

# Setup Cloud Logging
client = Client()
handler = CloudLoggingHandler(client)
cloud_logger = logging.getLogger('aegis')
cloud_logger.setLevel(logging.INFO)
cloud_logger.addHandler(handler)

# Structured logging
def log_incident(incident_id, severity, location):
    cloud_logger.info(
        "Incident detected",
        extra={
            "json_fields": {
                "incident_id": incident_id,
                "severity": severity,
                "location": location,
                "service": "aegis-api",
                "version": "1.0.0"
            }
        }
    )

# Usage
log_incident("INC-2025-001", "critical", {"lat": 37.7749, "lon": -122.4194})
```

### 11.4 Custom Metrics

```python
from google.cloud import monitoring_v3
import time

client = monitoring_v3.MetricServiceClient()
project_name = f"projects/PROJECT_ID"

# Create custom metric descriptor
descriptor = monitoring_v3.MetricDescriptor()
descriptor.type = "custom.googleapis.com/aegis/incidents_detected"
descriptor.metric_kind = monitoring_v3.MetricDescriptor.MetricKind.GAUGE
descriptor.value_type = monitoring_v3.MetricDescriptor.ValueType.INT64
descriptor.description = "Number of incidents detected by AEGIS"

descriptor = client.create_metric_descriptor(
    name=project_name,
    metric_descriptor=descriptor
)

# Write metric data
series = monitoring_v3.TimeSeries()
series.metric.type = "custom.googleapis.com/aegis/incidents_detected"
series.resource.type = "global"
series.points = [{
    "value": {"int64_value": 42},
    "interval": {"end_time": {"seconds": int(time.time())}}
}]

client.create_time_series(name=project_name, time_series=[series])
```

### 11.5 Error Reporting

```python
from google.cloud import error_reporting

error_client = error_reporting.Client()

try:
    # Your code here
    process_surveillance_feed()
except Exception as e:
    error_client.report_exception()
    # Or manual reporting
    error_client.report(
        error_reporting.build_error_message(e),
        http_context=error_reporting.HTTPContext(
            url="/api/incidents",
            method="POST",
            response_status_code=500
        )
    )
```

### 11.6 Distributed Tracing

```python
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Setup Cloud Trace
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

cloud_trace_exporter = CloudTraceSpanExporter()
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(cloud_trace_exporter)
)

# Instrument code
with tracer.start_as_current_span("process_incident") as span:
    span.set_attribute("incident.id", "INC-001")
    span.set_attribute("incident.severity", "critical")
    
    with tracer.start_as_current_span("ml_analysis"):
        run_ml_model()
    
    with tracer.start_as_current_span("save_to_firestore"):
        save_incident()
```

---

## 12. QUICK START COMMANDS

### 12.1 Project Initialization

```bash
# Set project and region
export PROJECT_ID="your-project-id"
export REGION="us-central1"
gcloud config set project $PROJECT_ID
gcloud config set run/region $REGION

# Enable all APIs
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  monitoring.googleapis.com \
  logging.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com

# Create Artifact Registry repository
gcloud artifacts repositories create aegis-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="AEGIS container images"

# Configure Docker auth
gcloud auth configure-docker ${REGION}-docker.pkg.dev
```

### 12.2 Deploy Complete Stack

```bash
# Deploy API service
gcloud run deploy aegis-api \
  --source . \
  --allow-unauthenticated \
  --memory=1Gi \
  --cpu=1 \
  --max-instances=10 \
  --set-env-vars="ENV=production,PROJECT_ID=$PROJECT_ID"

# Deploy ML service with GPU
gcloud beta run deploy aegis-ml \
  --image=${REGION}-docker.pkg.dev/$PROJECT_ID/aegis-repo/sam3d:latest \
  --gpu=1 \
  --gpu-type=nvidia-l4 \
  --memory=16Gi \
  --cpu=4 \
  --no-cpu-throttling \
  --max-instances=1 \
  --execution-environment=gen2

# Create Firestore database
gcloud firestore databases create \
  --location=$REGION \
  --type=firestore-native

# Create Cloud Storage buckets
gcloud storage buckets create gs://$PROJECT_ID-aegis-incidents \
  --location=$REGION \
  --default-storage-class=STANDARD
```

---

## 13. CONCLUSION & RECOMMENDATIONS

### 13.1 Architecture Summary

For the AEGIS project, we recommend:

1. **Cloud Run** for API services and ML inference (with GPU for SAM 3D)
2. **Firestore** for real-time incident tracking and sensor data
3. **Cloud Storage** with lifecycle policies for media archiving
4. **Vertex AI** for custom model training and predictions
5. **Cloud Build** for CI/CD automation
6. **Cloud Operations** for monitoring and logging

### 13.2 Cost Projections

With $4,000 in credits:
- **Development Phase (10 days):** ~$200-400
- **First Month (moderate usage):** ~$200-400
- **Remaining Credits:** ~$3,200-3,600 for extended development and scaling

### 13.3 Next Steps

1. Set up GCP project and enable APIs
2. Create service accounts with least privilege
3. Deploy initial Cloud Run services
4. Configure Firestore schema
5. Set up Cloud Build pipeline
6. Deploy SAM 3D to Cloud Run GPU
7. Implement monitoring and alerting

---

## REFERENCES

- [Cloud Run GPU Documentation](https://cloud.google.com/run/docs/configuring/services/gpu)
- [Vertex AI Documentation](https://cloud.google.com/vertex-ai/docs)
- [Firestore Pricing](https://cloud.google.com/firestore/pricing)
- [Cloud Storage Pricing](https://cloud.google.com/storage/pricing)
- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [GCP Free Tier](https://cloud.google.com/free)

---

*Report generated for AEGIS Project - Predictive Digital Risk Twin*
