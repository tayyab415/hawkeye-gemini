# Stage 1: Build frontend
FROM node:20 as frontend-builder
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Build backend
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    google-adk \
    google-genai \
    google-cloud-bigquery \
    google-cloud-firestore \
    google-cloud-storage \
    google-api-python-client \
    google-auth \
    earthengine-api \
    fastapi \
    uvicorn \
    python-dotenv \
    requests \
    pyarrow \
    googlemaps \
    shapely \
    pyproj

# Copy backend code
COPY app/ ./app/
COPY credentials/ ./credentials/
COPY data/ ./data/

# Copy the built frontend
COPY --from=frontend-builder /src/frontend/dist ./frontend/dist

# Copy .env files
COPY .env ./
COPY frontend/.env ./frontend/.env

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
