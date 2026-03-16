# Gemini Standard API Quick Reference for AEGIS

## Model Selection Cheat Sheet

| Task | Recommended Model | Alternative | Cost/Image or per 1M Tokens |
|------|-------------------|-------------|----------------------------|
| Quick risk analysis | `gemini-2.5-flash` | `gemini-2.5-flash-lite` | $0.30 input / $2.50 output |
| Detailed reports | `gemini-2.5-pro` | `gemini-3-pro` | $1.25 input / $10 output |
| Risk visualizations | `gemini-3.1-flash-image-preview` | `gemini-3-pro-image-preview` | $0.039/image ($0.0195 batch) |
| Premium visualizations | `gemini-3-pro-image-preview` | - | $0.134-$0.24/image |
| Batch processing | Any with Batch API | - | 50% discount |
| High volume | `gemini-2.5-flash-lite` | - | $0.075 input / $0.30 output |

## Quick Code Snippets

### Initialize Client
```python
from google import genai
client = genai.Client(api_key='YOUR_API_KEY')
```

### Simple Text Generation
```python
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='Analyze flood risk for Miami'
)
print(response.text)
```

### Image Analysis
```python
import base64
with open('image.jpg', 'rb') as f:
    image_data = base64.b64encode(f.read()).decode('utf-8')

response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=[
        types.Part.from_text('Analyze this image'),
        types.Part.from_bytes(data=image_data, mime_type='image/jpeg')
    ]
)
```

### Generate Risk Visualization
```python
response = client.models.generate_content(
    model='gemini-3.1-flash-image-preview',
    contents='Generate a flood risk map for Miami',
    config=types.GenerateContentConfig(
        response_modalities=['IMAGE', 'TEXT']
    )
)
# Save image from response...
```

### Grounding with Google Search
```python
grounding_tool = types.Tool(google_search=types.GoogleSearch())

response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='Current wildfire conditions in California',
    config=types.GenerateContentConfig(tools=[grounding_tool])
)
```

### Batch Processing (50% Savings)
```python
# Create batch job
batch_job = client.batches.create(
    model='gemini-2.5-flash',
    src='uploaded_file_name',
    config={'display_name': 'my-batch-job'}
)

# Poll for completion
while batch_job.state != 'JOB_STATE_SUCCEEDED':
    time.sleep(30)
    batch_job = client.batches.get(name=batch_job.name)

# Download results
results = client.files.download(file=batch_job.dest.file_name)
```

## Rate Limits (Tier 1 - Paid)

| Model | RPM | TPM | RPD | IPM |
|-------|-----|-----|-----|-----|
| Gemini 2.5 Pro | 150 | 1M | 1,500 | 10 |
| Gemini 2.5 Flash | 200 | 1M | 1,500 | 10 |
| Gemini 2.5 Flash-Lite | 300 | 1M | 1,500 | 10 |

## Pricing Summary

### Text Models (per 1M tokens)
- **Gemini 2.5 Flash**: $0.30 input / $2.50 output
- **Gemini 2.5 Pro**: $1.25 input / $10 output
- **Gemini 2.5 Flash-Lite**: $0.075 input / $0.30 output

### Image Generation (per image)
- **Nano Banana 2 (3.1 Flash)**: $0.039 ($0.0195 batch)
- **Nano Banana Pro (3 Pro)**: $0.134-$0.24 ($0.067-$0.12 batch)
- **Imagen 4 Fast**: $0.02 (no batch)

### Additional Services
- **Grounding (Search)**: $35 per 1,000 requests (1,500 RPD free)
- **Context Caching**: $0.025-$0.625 per 1M tokens + $1/hour storage
- **Text Embeddings**: FREE

## Budget Calculator ($4,000)

| Scenario | Images | Text Tokens | Cost |
|----------|--------|-------------|------|
| Image-heavy | 100,000 (batch) | 1M | ~$2,250 |
| Balanced | 50,000 (batch) | 5M | ~$2,400 |
| Text-heavy | 10,000 (batch) | 10M | ~$2,200 |

## Error Handling Pattern

```python
def generate_with_retry(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
        except Exception as e:
            if '429' in str(e) and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
```

## When to Use What

### Use Standard API for:
- Batch risk assessments
- Image generation
- Historical data analysis
- Report generation
- Non-real-time dashboards

### Use Live API for:
- Real-time voice interaction
- Live monitoring
- Interactive emergency response
- Continuous sensor streams

## Key Documentation Links

- [Gemini API Docs](https://ai.google.dev/gemini-api/docs)
- [Python SDK](https://github.com/googleapis/python-genai)
- [Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Batch API](https://ai.google.dev/gemini-api/docs/batch-api)
- [Grounding](https://ai.google.dev/gemini-api/docs/google-search)

## Support

- Google AI Studio: [aistudio.google.com](https://aistudio.google.com)
- Developer Forum: [discuss.ai.google.dev](https://discuss.ai.google.dev)
- Cloud Support: [cloud.google.com/support](https://cloud.google.com/support)
