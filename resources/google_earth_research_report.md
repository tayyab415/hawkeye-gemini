# AEGIS Project: Google Earth & Google Earth Engine API Research Report

## Executive Summary

This comprehensive research report provides detailed technical findings on Google Earth Engine (GEE) capabilities for the AEGIS surveillance and disaster response project. The research covers API capabilities, imagery extraction methods, pricing for the $4,000 GCP budget, and practical implementation guidance for infrastructure monitoring, bridge inspection, and flood damage assessment use cases.

---

## 1. Google Earth Engine API Capabilities

### 1.1 Core Platform Features

Google Earth Engine is a cloud-based platform for geospatial analysis that provides:

- **90+ petabytes of analysis-ready geospatial data** in the public catalog
- **50+ years of historical imagery** (Landsat archive from 1972)
- **Daily updates** with new satellite acquisitions
- **Serverless computing** - no local infrastructure needed
- **Python and JavaScript APIs** for programmatic access
- **Integration with Google Cloud services** (BigQuery, Cloud Storage, Vertex AI)

### 1.2 Available Data Types

| Dataset | Resolution | Revisit Frequency | Temporal Coverage |
|---------|------------|-------------------|-------------------|
| **Landsat 8/9** | 30m | 16 days | 2013-present |
| **Sentinel-2** | 10m (RGB/NIR), 20m, 60m | 5 days (combined) | 2015-present |
| **Sentinel-1 SAR** | 10m (IW mode) | 6-12 days | 2014-present |
| **NAIP (USA)** | 0.6m (2018+), 1m (pre-2018) | 3-year cycle | 2003-present |
| **Planet SkySat** | 0.5m (50cm) | On-demand tasking | 2014-present |
| **MODIS** | 250m-1km | Daily | 2000-present |
| **VIIRS** | 375m-750m | Daily | 2012-present |

### 1.3 Key API Capabilities for AEGIS

1. **Image Collection Processing**: Filter by date, bounds, cloud cover
2. **Spectral Indices**: NDVI, NDWI, NDBI, MNDWI for change detection
3. **Time Series Analysis**: Multi-temporal change detection
4. **Machine Learning**: Built-in Random Forest, SVM, and TensorFlow/PyTorch integration
5. **Export Capabilities**: GeoTIFF, TFRecord, Cloud Optimized GeoTIFF formats
6. **BigQuery Integration**: Direct SQL queries on geospatial data
7. **Vertex AI Integration**: Deploy ML models for inference

---

## 2. Photorealistic Aerial Imagery Extraction

### 2.1 High-Resolution Options

#### NAIP (National Agriculture Imagery Program) - USA Only
- **Resolution**: 0.6 meters (post-2018), 1 meter (pre-2018)
- **Bands**: RGB + Near-Infrared (4-band)
- **Coverage**: Continental USA
- **Update Cycle**: 3 years
- **Best for**: Infrastructure inspection, vegetation analysis
- **GEE Dataset ID**: `USDA/NAIP/DOQQ`

#### Planet SkySat
- **Resolution**: 50cm (0.5m) for images after June 2020
- **Bands**: RGB + NIR + Panchromatic
- **Revisit**: Up to 10x daily with tasking
- **Coverage**: Global
- **Cost**: ~$12/km² for archive, higher for tasking
- **GEE Dataset IDs**: 
  - `SKYSAT/GEN-A/PUBLIC/ORTHO/RGB`
  - `SKYSAT/GEN-A/PUBLIC/ORTHO/MULTISPECTRAL`

#### Sentinel-2 (Free Option)
- **Resolution**: 10m (best available free option)
- **Bands**: 13 spectral bands
- **Revisit**: 5 days (combined S2A + S2B)
- **Coverage**: Global
- **GEE Dataset ID**: `COPERNICUS/S2_SR_HARMONIZED`

### 2.2 Python Code Example: Extracting High-Resolution Imagery

```python
import ee
import geemap

# Initialize Earth Engine
ee.Authenticate()
ee.Initialize()

# Define Area of Interest (AOI) - Example: Bridge location
aoi = ee.Geometry.Rectangle([
    -122.1899, 37.5010,  # min lon, min lat
    -122.0899, 37.6010   # max lon, max lat
])

# Method 1: NAIP High-Resolution Imagery (USA only)
naip = ee.ImageCollection('USDA/NAIP/DOQQ') \
    .filterBounds(aoi) \
    .filterDate('2022-01-01', '2023-12-31') \
    .mosaic()

# Method 2: Sentinel-2 (Global, 10m resolution)
s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(aoi) \
    .filterDate('2023-06-01', '2023-09-30') \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10)) \
    .median()

# Method 3: Planet SkySat (if available in GEE)
skysat = ee.ImageCollection('SKYSAT/GEN-A/PUBLIC/ORTHO/RGB') \
    .filterBounds(aoi) \
    .median()

# Visualization parameters
vis_naip = {'bands': ['R', 'G', 'B'], 'min': 0, 'max': 255}
vis_s2 = {'bands': ['B4', 'B3', 'B2'], 'min': 0, 'max': 3000}

# Create map
Map = geemap.Map()
Map.centerObject(aoi, 16)
Map.addLayer(naip, vis_naip, 'NAIP 0.6m')
Map.addLayer(s2, vis_s2, 'Sentinel-2 10m')
Map
```

### 2.3 Exporting High-Resolution Images

```python
# Export NAIP imagery to Google Drive
task = ee.batch.Export.image.toDrive(
    image=naip,
    description='bridge_inspection_naip',
    folder='AEGIS_exports',
    region=aoi,
    scale=0.6,  # NAIP native resolution
    crs='EPSG:4326',
    maxPixels=1e10,
    fileFormat='GeoTIFF',
    formatOptions={'cloudOptimized': True}
)
task.start()

# Monitor task status
print(f'Task ID: {task.id}')
print(f'Status: {task.status()}')
```

---

## 3. Historical Imagery & Change Detection

### 3.1 Time Series Analysis for Infrastructure Monitoring

```python
# Define time periods for comparison
pre_period = ['2020-01-01', '2020-12-31']
post_period = ['2023-01-01', '2023-12-31']

# Function to get median composite for a period
def get_period_composite(collection, period, aoi):
    return collection \
        .filterBounds(aoi) \
        .filterDate(period[0], period[1]) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) \
        .median() \
        .clip(aoi)

# Get pre and post images
s2_collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
pre_image = get_period_composite(s2_collection, pre_period, aoi)
post_image = get_period_composite(s2_collection, post_period, aoi)

# Calculate change using spectral indices
# NDVI change (vegetation/infrastructure changes)
ndvi_pre = pre_image.normalizedDifference(['B8', 'B4']).rename('NDVI_pre')
ndvi_post = post_image.normalizedDifference(['B8', 'B4']).rename('NDVI_post')
ndvi_change = ndvi_post.subtract(ndvi_pre).rename('NDVI_change')

# NDBI change (built-up area detection)
ndbi_pre = pre_image.normalizedDifference(['B11', 'B8']).rename('NDBI_pre')
ndbi_post = post_image.normalizedDifference(['B11', 'B8']).rename('NDBI_post')
ndbi_change = ndbi_post.subtract(ndbi_pre).rename('NDBI_change')
```

### 3.2 Flood Damage Assessment with NDWI

```python
# Flood detection using Modified Normalized Difference Water Index (MNDWI)
def detect_flood(pre_image, post_image, threshold=0.3):
    # MNDWI = (Green - SWIR) / (Green + SWIR)
    mndwi_pre = pre_image.normalizedDifference(['B3', 'B11']).rename('MNDWI_pre')
    mndwi_post = post_image.normalizedDifference(['B3', 'B11']).rename('MNDWI_post')
    
    # Water masks
    water_pre = mndwi_pre.gt(0).rename('water_pre')
    water_post = mndwi_post.gt(0).rename('water_post')
    
    # New flooding = water now that wasn't water before
    new_flood = water_post.And(water_pre.Not()).rename('new_flood')
    
    # Calculate flood area
    flood_area = new_flood.multiply(ee.Image.pixelArea()).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=10,
        maxPixels=1e10
    )
    
    return {
        'mndwi_pre': mndwi_pre,
        'mndwi_post': mndwi_post,
        'new_flood': new_flood,
        'flood_area_m2': flood_area.get('new_flood')
    }

# Example usage
flood_results = detect_flood(pre_image, post_image)
print(f"New flood area: {flood_results['flood_area_m2'].getInfo()} m²")
```

### 3.3 SAR-Based Flood Detection (All-Weather)

```python
# Sentinel-1 SAR for flood detection (works through clouds)
s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
    .filterBounds(aoi) \
    .filter(ee.Filter.eq('instrumentMode', 'IW')) \
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))

# Pre-flood SAR image
s1_pre = s1.filterDate('2023-01-01', '2023-01-15').mosaic()

# Post-flood SAR image  
s1_post = s1.filterDate('2023-03-01', '2023-03-15').mosaic()

# Flood detection using VV polarization
# Water has low backscatter in VV
vv_pre = s1_pre.select('VV')
vv_post = s1_post.select('VV')

# Significant decrease in backscatter indicates flooding
flood_sar = vv_pre.subtract(vv_post).gt(3).rename('flood_sar')
```

---

## 4. Resolution & Coverage Analysis

### 4.1 Comparison Table for AEGIS Use Cases

| Use Case | Recommended Dataset | Resolution | Cost | Coverage |
|----------|-------------------|------------|------|----------|
| **Bridge Inspection** | NAIP (USA) / SkySat | 0.6m / 0.5m | Free / $$$ | USA / Global |
| **Road Monitoring** | Sentinel-2 / NAIP | 10m / 0.6m | Free | Global |
| **Flood Assessment** | Sentinel-1 SAR + S2 | 10m / 10m | Free | Global |
| **Building Damage** | SkySat / NAIP | 0.5m / 0.6m | $$$ / Free | Global / USA |
| **Vegetation Change** | Sentinel-2 | 10m | Free | Global |
| **Large Area Survey** | Sentinel-2 | 10m | Free | Global |

### 4.2 Resolution vs. Drone Comparison

| Platform | Resolution | Coverage | Update Frequency | Weather Dependency |
|----------|------------|----------|------------------|-------------------|
| **Consumer Drone** | 2-5 cm | Limited (per flight) | On-demand | VFR only |
| **NAIP** | 60 cm | Continental USA | 3 years | Clear weather |
| **SkySat** | 50 cm | Global | On-demand tasking | Clear weather |
| **Sentinel-2** | 10 m | Global | 5 days | Clear weather |
| **Sentinel-1 SAR** | 10 m | Global | 6-12 days | All-weather |

**Key Insight**: For AEGIS, combine approaches:
- Use GEE for **broad area monitoring** and **change detection**
- Use drones for **high-detail inspection** of identified anomalies
- SAR for **all-weather flood monitoring**

---

## 5. Pricing & Quota Analysis for $4,000 Budget

### 5.1 Google Earth Engine Pricing Tiers

#### Non-Commercial (Recommended for AEGIS if eligible)

| Tier | Monthly EECU Hours | Storage | Cost | Requirements |
|------|-------------------|---------|------|--------------|
| **Community** | 150 hours | - | Free | Verified non-commercial |
| **Contributor** | 1,000 hours | - | Free (billing account required) | Non-commercial project |
| **Partner** | 100,000 hours | - | Free | High-impact sustainability work |

#### Commercial Pricing

| Tier | Monthly Cost | Batch EECU | Online EECU | Storage | Users |
|------|--------------|------------|-------------|---------|-------|
| **Basic** | $500/month | 100 hours | 33 hours | 100 GB | 2 |
| **Professional** | $2,000/month | 500 hours | 166 hours | 1 TB | 5 |
| **Premium** | Contact Google | Custom | Custom | Custom | Unlimited |

### 5.2 Usage-Based Pricing (On-Demand)

| Resource | Price |
|----------|-------|
| **Compute (Online/Batch)** | $0.40 per EECU-hour |
| **Storage** | $0.026 per GB/month |

### 5.3 Budget Analysis for AEGIS ($4,000 GCP Credits)

**Option 1: Non-Commercial Tier (Best Value)**
- If AEGIS qualifies as research/non-commercial: **$0 cost**
- 1,000 EECU hours/month sufficient for most analyses
- Can apply for Partner tier (100,000 hours) for large-scale processing

**Option 2: Commercial Basic Tier**
- Cost: $500/month
- Duration with $4,000: **8 months**
- Includes: 100 batch + 33 online EECU hours, 100 GB storage

**Option 3: Commercial Professional Tier**
- Cost: $2,000/month
- Duration with $4,000: **2 months**
- Includes: 500 batch + 166 online EECU hours, 1 TB storage

**Recommendation**: Start with Non-Commercial tier. If commercial use is required, Basic tier provides best value for AEGIS scope.

### 5.4 External Data Costs

| Service | Cost | Notes |
|---------|------|-------|
| **Planet SkySat Archive** | ~$12/km² | High-resolution tasking |
| **Google Cloud Storage** | $0.02/GB/month | For exported data |
| **BigQuery** | $5/TB queried | For geospatial analysis |

---

## 6. Google Cloud Service Integrations

### 6.1 BigQuery Integration

```python
# Load BigQuery table into Earth Engine
bq_table = ee.FeatureCollection.loadBigQueryTable(
    'your-project:dataset.table',
    'geometry_column'
)

# Run BigQuery SQL from Earth Engine
query_result = ee.FeatureCollection.runBigQuery('
    SELECT * FROM `your-project.dataset.infrastructure`
    WHERE type = "bridge"
')

# Export Earth Engine results to BigQuery
task = ee.batch.Export.table.toBigQuery(
    collection=analysis_results,
    table='your-project:aegis.results',
    description='export_to_bigquery'
)
task.start()
```

### 6.2 Cloud Storage Integration

```python
# Export to Cloud Storage
task = ee.batch.Export.image.toCloudStorage(
    image=processed_image,
    description='export_to_gcs',
    bucket='aegis-satellite-data',
    fileNamePrefix='flood_analysis/2024/',
    region=aoi,
    scale=10,
    maxPixels=1e10
)
task.start()
```

### 6.3 Vertex AI Integration

```python
# Deploy TensorFlow model to Vertex AI for inference
# Train model in Earth Engine using TensorFlow
model = ee.Model.fromVertexAi(
    endpoint='projects/your-project/locations/us-central1/endpoints/your-endpoint'
)

# Run predictions
predictions = model.predictImage(image)
```

---

## 7. Infrastructure Monitoring Use Cases

### 7.1 Bridge Inspection Workflow

```python
# Step 1: Define bridge location
bridge_location = ee.Geometry.Point([-122.1899, 37.5010])
bridge_buffer = bridge_location.buffer(500)  # 500m buffer

# Step 2: Get high-resolution imagery
naip_bridge = ee.ImageCollection('USDA/NAIP/DOQQ') \
    .filterBounds(bridge_buffer) \
    .sort('system:time_start', False) \
    .first()

# Step 3: Calculate vegetation encroachment (NDVI)
ndvi = naip_bridge.normalizedDifference(['N', 'R'])
vegetation_mask = ndvi.gt(0.4)  # High vegetation

# Step 4: Detect changes over time
naip_2018 = ee.ImageCollection('USDA/NAIP/DOQQ') \
    .filterBounds(bridge_buffer) \
    .filterDate('2018-01-01', '2018-12-31') \
    .mosaic()

naip_2022 = ee.ImageCollection('USDA/NAIP/DOQQ') \
    .filterBounds(bridge_buffer) \
    .filterDate('2022-01-01', '2022-12-31') \
    .mosaic()

# Structural change detection
change = naip_2022.select(['R', 'G', 'B']).subtract(
    naip_2018.select(['R', 'G', 'B'])
).reduce(ee.Reducer.sum()).abs()

# Export for detailed analysis
task = ee.batch.Export.image.toDrive(
    image=change,
    description='bridge_change_detection',
    folder='AEGIS/bridges',
    region=bridge_buffer,
    scale=0.6,
    maxPixels=1e9
)
task.start()
```

### 7.2 Road Network Monitoring

```python
# Road condition assessment using NDBI (Normalized Difference Built-up Index)
def assess_road_condition(image, road_geometry):
    # NDBI highlights built-up areas
    ndbi = image.normalizedDifference(['B11', 'B8'])
    
    # Extract road condition along the route
    road_condition = ndbi.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=road_geometry,
        scale=10,
        maxPixels=1e9
    )
    
    return road_condition

# Example road geometry (simplified)
road = ee.Geometry.LineString([
    [-122.1899, 37.5010],
    [-122.1899, 37.5110],
    [-122.1999, 37.5210]
])

# Assess with Sentinel-2
s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(road) \
    .filterDate('2023-01-01', '2023-12-31') \
    .median()

condition = assess_road_condition(s2, road)
print(f"Road condition index: {condition.getInfo()}")
```

### 7.3 Flood Damage Assessment Pipeline

```python
class FloodAssessment:
    def __init__(self, aoi):
        self.aoi = aoi
        
    def get_pre_flood_image(self, flood_date):
        """Get image from 1 month before flood"""
        return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(self.aoi) \
            .filterDate(
                ee.Date(flood_date).advance(-1, 'month'),
                ee.Date(flood_date)
            ) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10)) \
            .median()
    
    def get_post_flood_image(self, flood_date):
        """Get image from 1-2 weeks after flood"""
        return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(self.aoi) \
            .filterDate(
                ee.Date(flood_date).advance(1, 'week'),
                ee.Date(flood_date).advance(2, 'week')
            ) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) \
            .median()
    
    def detect_flooding(self, pre_image, post_image):
        """Detect flooded areas using multiple indices"""
        # NDWI for water detection
        ndwi_pre = pre_image.normalizedDifference(['B3', 'B8'])
        ndwi_post = post_image.normalizedDifference(['B3', 'B8'])
        
        # Water masks
        water_pre = ndwi_pre.gt(0.1)
        water_post = ndwi_post.gt(0.1)
        
        # New flooding
        new_flood = water_post.And(water_pre.Not())
        
        # Calculate statistics
        flood_stats = new_flood.multiply(ee.Image.pixelArea()).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=self.aoi,
            scale=10,
            maxPixels=1e10
        )
        
        return {
            'flood_mask': new_flood,
            'flood_area_m2': flood_stats.get('constant'),
            'flood_percentage': flood_stats.get('constant').divide(
                self.aoi.area()
            ).multiply(100)
        }
    
    def assess_damage(self, flood_results, building_footprints):
        """Assess building damage in flooded areas"""
        # Buildings in flood zone
        flooded_buildings = building_footprints.filterBounds(
            flood_results['flood_mask'].geometry()
        )
        
        return flooded_buildings.size()

# Usage
flood_date = '2023-03-15'
assessment = FloodAssessment(aoi)
pre = assessment.get_pre_flood_image(flood_date)
post = assessment.get_post_flood_image(flood_date)
results = assessment.detect_flooding(pre, post)

print(f"Flood area: {results['flood_area_m2'].getInfo()} m²")
print(f"Flood percentage: {results['flood_percentage'].getInfo()}%")
```

---

## 8. Practical Implementation Recommendations

### 8.1 AEGIS Architecture with GEE

```
┌─────────────────────────────────────────────────────────────────┐
│                        AEGIS SYSTEM                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Drone      │    │  Satellite   │    │   Ground     │      │
│  │  Footage     │◄──►│   Data (GEE) │◄──►│   Sensors    │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│         │                   │                    │              │
│         └───────────────────┼────────────────────┘              │
│                             ▼                                   │
│                    ┌─────────────────┐                         │
│                    │  Google Earth   │                         │
│                    │     Engine      │                         │
│                    │  (Processing)   │                         │
│                    └────────┬────────┘                         │
│                             │                                   │
│              ┌──────────────┼──────────────┐                   │
│              ▼              ▼              ▼                   │
│       ┌──────────┐   ┌──────────┐   ┌──────────┐              │
│       │ BigQuery │   │ Cloud    │   │ Vertex   │              │
│       │ Analysis │   │ Storage  │   │   AI     │              │
│       └──────────┘   └──────────┘   └──────────┘              │
│                                              │                  │
│                                              ▼                  │
│                                    ┌─────────────────┐         │
│                                    │  Gemini Live    │         │
│                                    │  (Anomaly Det.) │         │
│                                    └─────────────────┘         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 Recommended Workflow

1. **Broad Area Monitoring (GEE)**
   - Use Sentinel-2 (10m) for weekly change detection
   - Use Sentinel-1 SAR for all-weather monitoring
   - Identify areas of interest/anomalies

2. **Targeted High-Res Analysis (GEE)**
   - Use NAIP (0.6m) for USA infrastructure
   - Use SkySat tasking (0.5m) for critical areas
   - Generate change detection maps

3. **Detailed Inspection (Drones)**
   - Deploy drones to GEE-identified anomalies
   - Capture cm-resolution imagery
   - Feed to Gemini Live API for real-time analysis

4. **Data Integration**
   - Export GEE results to Cloud Storage
   - Analyze in BigQuery
   - Train ML models in Vertex AI
   - Visualize in custom dashboards

### 8.3 Code Repository Structure

```
aegis-gee/
├── README.md
├── requirements.txt
├── config/
│   └── gee_config.py
├── src/
│   ├── __init__.py
│   ├── authentication.py
│   ├── image_collection.py
│   ├── change_detection.py
│   ├── flood_detection.py
│   ├── infrastructure.py
│   └── export.py
├── notebooks/
│   ├── 01_introduction.ipynb
│   ├── 02_bridge_inspection.ipynb
│   ├── 03_flood_assessment.ipynb
│   └── 04_change_detection.ipynb
├── scripts/
│   ├── batch_export.py
│   └── monitoring_pipeline.py
└── tests/
    └── test_modules.py
```

---

## 9. Limitations & Considerations

### 9.1 Satellite Imagery Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **Cloud cover** | Blocks optical sensors | Use SAR (Sentinel-1) |
| **Resolution** | 10m may miss small features | Use NAIP/SkySat |
| **Revisit time** | Days between images | Combine multiple sensors |
| **Atmospheric effects** | Affects spectral accuracy | Use surface reflectance products |
| **Cost** | High-res imagery expensive | Use free tiers, task strategically |

### 9.2 GEE Quotas & Limits

| Limit | Value | Notes |
|-------|-------|-------|
| **Concurrent tasks** | 8-20 (tier dependent) | Batch exports |
| **API requests** | 20-500 QPS (tier dependent) | Interactive requests |
| **maxPixels** | 1e13 default | Can be increased |
| **Export size** | No hard limit | Split large exports |
| **Asset storage** | 10 GB (free tier) | More available in paid tiers |

### 9.3 When to Use Drones vs GEE

| Scenario | Recommendation |
|----------|----------------|
| **Large area survey** | GEE (cost-effective) |
| **All-weather monitoring** | GEE SAR + limited drone |
| **cm-level detail needed** | Drone (primary), GEE (context) |
| **Rapid disaster response** | Both: GEE for scope, drone for detail |
| **Regular infrastructure inspection** | Hybrid: GEE for screening, drone for anomalies |

---

## 10. Summary & Recommendations

### For AEGIS Project ($4,000 Budget):

1. **Start with Non-Commercial GEE Tier**
   - 1,000 EECU hours/month at no cost
   - Sufficient for most analysis needs
   - Apply for Partner tier if needed

2. **Free Data Strategy**
   - Sentinel-2 (10m) for broad monitoring
   - Sentinel-1 SAR for all-weather flood detection
   - NAIP (0.6m) for USA infrastructure

3. **Strategic High-Res Purchases**
   - Reserve budget for SkySat tasking
   - ~$12/km² for critical infrastructure
   - Use for validation and detailed analysis

4. **Integration with Gemini Live API**
   - Use GEE for anomaly detection at scale
   - Export regions of interest
   - Feed to Gemini for detailed drone footage analysis

5. **Expected Outcomes**
   - 8+ months of full GEE access with $4,000
   - Global coverage for flood monitoring
   - USA high-res coverage for infrastructure
   - Scalable pipeline for continuous monitoring

---

## References

1. Google Earth Engine Documentation: https://developers.google.com/earth-engine
2. Earth Engine Data Catalog: https://developers.google.com/earth-engine/datasets
3. GEE Pricing: https://cloud.google.com/earth-engine/pricing
4. Sentinel-2 Documentation: https://sentinels.copernicus.eu/web/sentinel/missions/sentinel-2
5. Planet SkySat Specifications: https://docs.planet.com/data/imagery/skysat/
6. NAIP Program: https://www.usgs.gov/centers/eros/science/usgs-eros-archive-aerial-photography-national-agriculture-imagery-program-naip

---

*Report generated for AEGIS Project - Predictive Digital Risk Twin for Disaster Response*
