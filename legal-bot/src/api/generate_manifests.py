"""
generate_manifests.py

Scans all Case_*/supporting_docs/ folders in the blob container,
calls GPT-4o Vision for each image not yet in a manifest,
writes/updates _manifest.json per case, then triggers indexer reset.

Run from src/api/:
    python generate_manifests.py
"""

import asyncio
import base64
import json
import logging
import os
import re
from datetime import date

from azure.identity import AzureDeveloperCliCredential
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import ContentSettings
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import (
    UserMessage,
    ImageContentItem,
    TextContentItem,
    ImageUrl,
)
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes.aio import SearchIndexerClient
from openai import AsyncAzureOpenAI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("generate_manifests")

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


async def generate_metadata(
    image_bytes: bytes,
    content_type: str,
    filename: str,
    case_id: str,
    openai_client: AsyncAzureOpenAI,
    chat_model: str,
) -> dict:
    """Call GPT-4o Vision to generate semantic metadata for an image."""
    b64 = base64.b64encode(image_bytes).decode()

    prompt = (
        f"You are labeling an image uploaded to legal case {case_id}.\n"
        f"Original filename: {filename}\n\n"
        f"Analyze the image and generate:\n"
        f"- filename: snake_case, no extension, max 8 words, use specific visible "
        f"details (locations, objects, scene type)\n"
        f"- description: one factual sentence describing what is shown\n"
        f"- tags: up to 6 short strings useful for search\n\n"
        f"Respond in JSON only, no markdown fences:\n"
        f"{{\"filename\": \"...\", \"description\": \"...\", \"tags\": [...]}}"
    )

    try:
        response = await openai_client.chat.completions.create(
            model=chat_model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{b64}"}
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }],
            temperature=0.0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group()) if match else {}
        logger.info(f"Vision succeeded for {filename}: {result.get('description', '')}")
    except Exception as e:
        logger.warning(f"Vision call failed for {filename}: {e}")
        result = {}

    ext = os.path.splitext(filename)[1].lower()
    raw_name = result.get("filename", "") or os.path.splitext(filename)[0]
    safe_name = re.sub(r'[^\w]+', '_', raw_name).strip('_').lower()
    semantic_filename = f"{safe_name}{ext}"

    return {
        "semantic_filename": semantic_filename,
        "description": result.get("description", filename),
        "tags": result.get("tags", []),
    }


async def process_case(
    case_id: str,
    container_client,
    openai_client: AsyncAzureOpenAI,
    chat_model: str,
):
    manifest_path = f"{case_id}/supporting_docs/_manifest.json"
    prefix = f"{case_id}/supporting_docs/"

    # Load existing manifest or start fresh
    try:
        blob_client = container_client.get_blob_client(manifest_path)
        download = await blob_client.download_blob()
        existing_data = await download.readall()
        manifest = json.loads(existing_data)
        logger.info(f"[{case_id}] Loaded existing manifest ({len(manifest['images'])} images)")
    except Exception:
        manifest = {"case_id": case_id, "images": []}
        logger.info(f"[{case_id}] No existing manifest — creating fresh")

    existing_paths = {img["path"] for img in manifest["images"]}

    new_count = 0
    async for blob in container_client.list_blobs(name_starts_with=prefix):
        blob_name = blob.name

        if blob_name.endswith("_manifest.json"):
            continue

        ext = os.path.splitext(blob_name)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            logger.info(f"[{case_id}] Skipping unsupported file: {blob_name}")
            continue

        if blob_name in existing_paths:
            logger.info(f"[{case_id}] Already in manifest: {blob_name}")
            continue

        logger.info(f"[{case_id}] Processing: {blob_name}")
        try:
            dl = await container_client.get_blob_client(blob_name).download_blob()
            image_bytes = await dl.readall()
        except Exception as e:
            logger.error(f"[{case_id}] Failed to download {blob_name}: {e}")
            continue

        content_type = EXT_TO_MIME.get(ext, "image/jpeg")
        original_filename = os.path.basename(blob_name)

        meta = await generate_metadata(
            image_bytes=image_bytes,
            content_type=content_type,
            filename=original_filename,
            case_id=case_id,
            openai_client=openai_client,
            chat_model=chat_model,
        )

        manifest["images"].append({
            "filename": meta["semantic_filename"],
            "path": blob_name,
            "title": meta["description"],
            "tags": meta["tags"],
            "uploaded_at": str(date.today()),
            "original_filename": original_filename,
        })
        new_count += 1
        logger.info(f"[{case_id}] Added: {meta['semantic_filename']} — {meta['description']}")

    if new_count == 0:
        logger.info(f"[{case_id}] No new images found — skipping manifest write")
        return False

    await container_client.upload_blob(
        name=manifest_path,
        data=json.dumps(manifest, indent=2).encode(),
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    logger.info(f"[{case_id}] Manifest written — {new_count} new images added")
    return True


async def trigger_indexer_reset():
    indexer_name = os.environ["AZURE_AI_SEARCH_INDEXER_NAME"]
    endpoint = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
    key = os.environ["AZURE_SEARCH_ADMIN_KEY"]

    async with SearchIndexerClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key)
    ) as client:
        await client.reset_indexer(indexer_name)
        await client.run_indexer(indexer_name)
        logger.info(f"Indexer '{indexer_name}' reset and run triggered")


async def main():
    storage_account = os.environ["AZURE_STORAGE_ACCOUNT"]
    container_name = os.environ["AZURE_STORAGE_CONTAINER"]
    chat_model = os.environ["AZURE_AI_CHAT_DEPLOYMENT_NAME"]
    openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]

    credential = AzureDeveloperCliCredential(tenant_id=os.environ["AZURE_TENANT_ID"])
    account_url = f"https://{storage_account}.blob.core.windows.net"

    # Use AsyncAzureOpenAI with API key — avoids Foundry audience auth issues
    openai_client = AsyncAzureOpenAI(
        azure_endpoint=openai_endpoint,
        api_key=openai_api_key,
        api_version="2024-02-15-preview",
    )

    async with BlobServiceClient(
        account_url=account_url,
        credential=credential,
    ) as blob_service:
        container_client = blob_service.get_container_client(container_name)

        # Discover all case folders with supporting_docs
        case_ids = set()
        async for blob in container_client.list_blobs():
            parts = blob.name.split("/")
            if len(parts) >= 3 and parts[1] == "supporting_docs":
                case_ids.add(parts[0])

        if not case_ids:
            logger.info("No supporting_docs folders found in any case")
            await openai_client.close()
            return

        logger.info(f"Found {len(case_ids)} case(s) with supporting_docs: {sorted(case_ids)}")

        any_updated = False
        for case_id in sorted(case_ids):
            updated = await process_case(
                case_id=case_id,
                container_client=container_client,
                openai_client=openai_client,
                chat_model=chat_model,
            )
            if updated:
                any_updated = True

        if any_updated:
            await trigger_indexer_reset()
        else:
            logger.info("No changes — indexer reset skipped")

    await openai_client.close()
    logger.info("Done")


if __name__ == "__main__":
    asyncio.run(main())