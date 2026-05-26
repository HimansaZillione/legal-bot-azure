import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone, date
from typing import Dict
from urllib.parse import quote

import fastapi
from fastapi import Request, Depends, UploadFile, File, Form
from fastapi.responses import StreamingResponse, RedirectResponse
from azure.ai.inference.aio import ChatCompletionsClient
from azure.ai.inference.models import (
    UserMessage,
    ImageContentItem,
    TextContentItem,
    ImageUrl,
)
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import (
    generate_blob_sas,
    BlobSasPermissions,
    ContentSettings,
)

from util import get_logger, ChatRequest, Message
from search_index_manager import SearchIndexManager

logger = get_logger(
    name="legal_bot_routes",
    log_level=logging.INFO,
    log_file_name=os.getenv("APP_LOG_FILE"),
    log_to_console=True
)

router = fastapi.APIRouter()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
IMAGE_EXT_MAP = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


# ─── Dependencies ────────────────────────────────────────────────────────────

def get_chat_client(request: Request) -> ChatCompletionsClient:
    return request.app.state.chat


def get_chat_model(request: Request) -> str:
    return request.app.state.chat_model


def get_search_index_manager(request: Request) -> SearchIndexManager:
    return request.app.state.search_index_manager


def get_credential(request: Request):
    return request.app.state.credential


def serialize_sse_event(data: Dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _trigger_indexer_reset():
    """Resets and runs the AI Search indexer to pick up manifest changes."""
    indexer_name = os.getenv("AZURE_AI_SEARCH_INDEXER_NAME")
    if not indexer_name:
        logger.warning("AZURE_AI_SEARCH_INDEXER_NAME not set — skipping indexer reset")
        return
    try:
        from azure.search.documents.indexes.aio import SearchIndexerClient
        from azure.core.credentials import AzureKeyCredential
        endpoint = os.environ["AZURE_AI_SEARCH_ENDPOINT"]
        key = os.environ["AZURE_SEARCH_ADMIN_KEY"]
        async with SearchIndexerClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key)
        ) as client:
            await client.reset_indexer(indexer_name)
            await client.run_indexer(indexer_name)
            logger.info(f"Indexer '{indexer_name}' reset and run triggered")
    except Exception as e:
        logger.warning(f"Indexer reset failed (non-fatal): {e}")


async def _generate_semantic_metadata(
    image_bytes: bytes,
    content_type: str,
    user_title: str,
    case_id: str,
    search_manager: SearchIndexManager,
    chat_client: ChatCompletionsClient,
    chat_model: str,
) -> dict:
    """
    Uses GPT-4o Vision + existing case doc context to generate a semantic
    filename, description, and tags for the uploaded image.
    """
    # Pull relevant case context from already-indexed docs
    query = user_title.strip() if user_title.strip() else "overview of case documents"
    try:
        context, _ = await search_manager.search_with_citations(
            ChatRequest(messages=[Message(content=query)], case_id=case_id),
            case_id,
        )
    except Exception as e:
        logger.warning(f"Context fetch for semantic metadata failed: {e}")
        context = ""

    b64 = base64.b64encode(image_bytes).decode()

    prompt = (
        f"You are labeling an image uploaded to legal case {case_id}.\n\n"
        f"Relevant excerpts from this case's documents:\n"
        f"---\n{context[:2500]}\n---\n\n"
        f"User's label (may be blank): \"{user_title}\"\n\n"
        f"Analyze the image and the case context together. Generate:\n"
        f"- filename: snake_case, no extension, max 8 words, use specific case "
        f"entities (names, locations, reference numbers) where visible in the image\n"
        f"- description: one factual sentence referencing case entities where relevant\n"
        f"- tags: up to 6 short strings useful for search\n\n"
        f"Respond in JSON only, no markdown fences:\n"
        f"{{\"filename\": \"...\", \"description\": \"...\", \"tags\": [...]}}"
    )

    try:
        response = await chat_client.complete(
            model=chat_model,
            messages=[
                UserMessage(content=[
                    ImageContentItem(
                        image_url=ImageUrl(url=f"data:{content_type};base64,{b64}")
                    ),
                    TextContentItem(text=prompt),
                ])
            ],
            temperature=0.0,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group()) if match else {}
    except Exception as e:
        logger.warning(f"Vision metadata generation failed: {e}")
        result = {}

    # Sanitise filename
    ext = IMAGE_EXT_MAP.get(content_type, "jpg")
    raw_name = result.get("filename", "") or user_title or "image"
    safe_name = re.sub(r'[^\w]+', '_', raw_name).strip('_').lower()
    filename = f"{safe_name}.{ext}"

    return {
        "filename": filename,
        "description": result.get("description", user_title or filename),
        "tags": result.get("tags", []),
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/api/cases")
async def list_cases(
    search_index_manager: SearchIndexManager = Depends(get_search_index_manager)
):
    try:
        cases = await search_index_manager.get_all_cases()
        return {"cases": cases}
    except Exception as e:
        logger.error(f"Error fetching cases: {e}")
        raise fastapi.HTTPException(status_code=500, detail=str(e))


@router.get("/api/document/{filename:path}")
async def get_document(filename: str, request: Request):
    """
    Resolves blob path to a short-lived SAS URL and redirects.
    Works for both documents and supporting_docs images.
    filename format: Case_1202/Case_1202_01.docx
                     Case_1202/supporting_docs/photo.jpg
    """
    try:
        storage_account = os.environ["AZURE_STORAGE_ACCOUNT"]
        container = os.environ["AZURE_STORAGE_CONTAINER"]
        credential = get_credential(request)
        account_url = f"https://{storage_account}.blob.core.windows.net"

        async with BlobServiceClient(
            account_url=account_url,
            credential=credential
        ) as blob_service:
            delegation_key = await blob_service.get_user_delegation_key(
                key_start_time=datetime.now(timezone.utc),
                key_expiry_time=datetime.now(timezone.utc) + timedelta(minutes=30)
            )
            sas_token = generate_blob_sas(
                account_name=storage_account,
                container_name=container,
                blob_name=filename,
                user_delegation_key=delegation_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(minutes=30)
            )
            blob_url = f"{account_url}/{container}/{filename}?{sas_token}"

            ext = filename.lower().split(".")[-1]
            if ext in ("docx", "doc", "xlsx", "pptx", "ppt"):
                viewer_url = (
                    f"https://view.officeapps.live.com/op/view.aspx"
                    f"?src={quote(blob_url, safe='')}"
                )
                return RedirectResponse(url=viewer_url)

            # Images and PDFs redirect directly to SAS URL
            return RedirectResponse(url=blob_url)

    except Exception as e:
        logger.error(f"Document fetch error: {e}")
        raise fastapi.HTTPException(status_code=500, detail=str(e))


@router.post("/api/images/upload")
async def upload_supporting_image(
    request: Request,
    case_id: str = Form(...),
    title: str = Form(""),
    tags: str = Form(""),          # comma-separated, optional
    file: UploadFile = File(...),
):
    """
    Uploads an image to Case_X/supporting_docs/, generates semantic metadata
    via GPT-4o Vision, and updates _manifest.json for that case.
    """
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise fastapi.HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Only JPEG, PNG, GIF, WEBP accepted."
        )

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise fastapi.HTTPException(
            status_code=400,
            detail="Image exceeds 10 MB limit."
        )

    storage_account = os.environ["AZURE_STORAGE_ACCOUNT"]
    container = os.environ["AZURE_STORAGE_CONTAINER"]
    credential = get_credential(request)
    account_url = f"https://{storage_account}.blob.core.windows.net"

    # Generate semantic filename + description + tags via GPT-4o Vision
    meta = await _generate_semantic_metadata(
        image_bytes=data,
        content_type=file.content_type,
        user_title=title,
        case_id=case_id,
        search_manager=request.app.state.search_index_manager,
        chat_client=request.app.state.chat,
        chat_model=request.app.state.chat_model,
    )

    image_blob_path = f"{case_id}/supporting_docs/{meta['filename']}"
    manifest_blob_path = f"{case_id}/supporting_docs/_manifest.json"

    # Merge user-supplied tags with AI-generated tags
    user_tags = [t.strip() for t in tags.split(",") if t.strip()]
    all_tags = list(dict.fromkeys(meta["tags"] + user_tags))  # deduplicate, preserve order

    async with BlobServiceClient(
        account_url=account_url,
        credential=credential
    ) as blob_service:
        container_client = blob_service.get_container_client(container)

        # Upload the image
        await container_client.upload_blob(
            name=image_blob_path,
            data=data,
            overwrite=True,
            content_settings=ContentSettings(content_type=file.content_type),
        )
        logger.info(f"Image uploaded: {image_blob_path}")

        # Read existing manifest or create fresh
        try:
            blob_client = container_client.get_blob_client(manifest_blob_path)
            download = await blob_client.download_blob()
            existing_data = await download.readall()
            manifest = json.loads(existing_data)
        except Exception:
            manifest = {"case_id": case_id, "images": []}

        # Append new entry
        manifest["images"].append({
            "filename": meta["filename"],
            "path": image_blob_path,
            "title": meta["description"],
            "tags": all_tags,
            "uploaded_at": str(date.today()),
        })

        # Write manifest back
        await container_client.upload_blob(
            name=manifest_blob_path,
            data=json.dumps(manifest, indent=2).encode(),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        logger.info(f"Manifest updated: {manifest_blob_path} ({len(manifest['images'])} images)")

    # Trigger indexer reset so updated manifest is searchable
    await _trigger_indexer_reset()

    return {
        "status": "ok",
        "path": image_blob_path,
        "title": meta["description"],
        "tags": all_tags,
    }


@router.post("/api/chat")
async def chat_stream_handler(
    chat_request: ChatRequest,
    chat_client: ChatCompletionsClient = Depends(get_chat_client),
    model_deployment_name: str = Depends(get_chat_model),
    search_index_manager: SearchIndexManager = Depends(get_search_index_manager),
) -> StreamingResponse:

    case_id = chat_request.case_id

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream"
    }

    async def response_stream():
        messages = [
            {"role": m.role, "content": m.content}
            for m in chat_request.messages
        ]

        system_prompt = (
            f"You are a legal assistant AI for a law firm.\n"
            f"You are answering questions about case {case_id} only.\n"
            f"Do not reference any other case.\n"
            f"If you cannot find the answer, say so honestly."
        )

        citations = []

        if search_index_manager is not None:
            context, citations = await search_index_manager.search_with_citations(
                chat_request, case_id
            )
            if context:
                system_prompt = (
                    f"You are a legal assistant AI for a law firm.\n"
                    f"You are answering questions about case {case_id} ONLY.\n"
                    f"Answer ONLY using the context below. Always cite the document title.\n"
                    f"If the answer is not in the context, say so — do not guess.\n"
                    f"Do not reference any other case.\n\n"
                    f"IMPORTANT: At the very end of your response append these two lines:\n"
                    f"SOURCES_USED: [comma separated list of document filenames you actually referenced]\n"
                    f"If the context contains image entries from _manifest.json and the user is asking "
                    f"about images or visual evidence, also append on the next line:\n"
                    f"IMAGES_REFERENCED: [comma separated list of exact image paths from the context]\n"
                    f"Only include images directly relevant to the question. "
                    f"If no images are relevant, omit IMAGES_REFERENCED entirely.\n\n"
                    f"CONTEXT:\n{context}"
                )
                logger.info(
                    f"Retrieved context for case {case_id}, "
                    f"{len(context)} chars, {len(citations)} citations"
                )
            else:
                logger.info(f"No context found for case {case_id}")

        prompt_messages = [{"role": "system", "content": system_prompt}]

        try:
            accumulated = ""
            chat_coroutine = await chat_client.complete(
                model=model_deployment_name,
                messages=prompt_messages + messages,
                stream=True,
                temperature=0.2,
            )
            async for event in chat_coroutine:
                if event.choices:
                    delta = event.choices[0].delta.content
                    if delta:
                        accumulated += delta
                        yield serialize_sse_event({
                            "content": delta,
                            "type": "message"
                        })

            # ── Parse SOURCES_USED and IMAGES_REFERENCED ──────────────────
            clean_response = accumulated
            filtered_citations = citations
            image_refs = []

            if "SOURCES_USED:" in accumulated:
                parts = accumulated.split("SOURCES_USED:", 1)
                clean_response = parts[0].strip()
                remainder = parts[1].strip()

                # Split off IMAGES_REFERENCED if present
                if "IMAGES_REFERENCED:" in remainder:
                    sources_line, images_line = remainder.split("IMAGES_REFERENCED:", 1)
                else:
                    sources_line = remainder
                    images_line = ""

                # Parse sources
                sources_line = sources_line.strip().strip("[]")
                used_titles = [
                    t.strip().strip("'\"")
                    for t in sources_line.split(",")
                    if t.strip()
                ]
                filtered_citations = [
                    c for c in citations
                    if any(
                        used in c["title"] or c["title"] in used
                        for used in used_titles
                    )
                ]

                # Parse image paths
                if images_line.strip():
                    raw_paths = images_line.strip().strip("[]").split(",")
                    image_refs = [
                        {
                            "path": p.strip().strip("'\""),
                            "title": p.strip().split("/")[-1].strip("'\""),
                        }
                        for p in raw_paths
                        if p.strip().strip("'\"")
                    ]

                logger.info(f"Citations: {[c['title'] for c in filtered_citations]}")
                logger.info(f"Images referenced: {[r['path'] for r in image_refs]}")

            # ── Send completed message (SOURCES/IMAGES lines stripped) ──────
            yield serialize_sse_event({
                "content": clean_response,
                "type": "completed_message"
            })

            # ── Send citations ─────────────────────────────────────────────
            if filtered_citations:
                yield serialize_sse_event({
                    "type": "citations",
                    "citations": filtered_citations
                })

            # ── Send image refs ────────────────────────────────────────────
            if image_refs:
                yield serialize_sse_event({
                    "type": "images",
                    "images": image_refs
                })

        except Exception as e:
            error_text = str(e)
            logger.error(f"Chat error: {error_text}")
            yield serialize_sse_event({
                "content": error_text,
                "type": "completed_message"
            })

        yield serialize_sse_event({"type": "stream_end"})

    return StreamingResponse(response_stream(), headers=headers)