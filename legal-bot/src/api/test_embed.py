
import os
import logging
from urllib.parse import urlparse
from dotenv import load_dotenv
from azure.identity import AzureDeveloperCliCredential
from azure.ai.inference import EmbeddingsClient

# Enable full HTTP debug logging
logging.basicConfig(level=logging.DEBUG)

load_dotenv(override=True)

tenant_id = os.getenv("AZURE_TENANT_ID")
credential = AzureDeveloperCliCredential(tenant_id=tenant_id)

endpoint = os.environ["AZURE_EXISTING_AIPROJECT_ENDPOINT"]
inference_endpoint = f"https://{urlparse(endpoint).netloc}/models"
model = os.getenv("AZURE_AI_EMBED_DEPLOYMENT_NAME")

print("=" * 60)
print(f"Project endpoint:   {endpoint}")
print(f"Inference endpoint: {inference_endpoint}")
print(f"Model:              {model}")
print("=" * 60)

# Using SYNC client (not aio) for simple testing
client = EmbeddingsClient(
    endpoint=inference_endpoint,
    credential=credential,
    credential_scopes=["https://ai.azure.com/.default"],
    logging_enable=True,  # This will show raw HTTP request/response
)

try:
    result = client.embed(
        input=["test query"],
        model=model,
    )
    print("\n✅ SUCCESS")
    print(f"Embedding length: {len(result.data[0].embedding)}")
except Exception as e:
    print(f"\n❌ FAILED: {repr(e)}")
finally:
    client.close()
 