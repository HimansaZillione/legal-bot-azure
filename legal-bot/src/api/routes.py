import json
import logging
import os
from typing import Dict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import fastapi
from fastapi import Request, Depends
from fastapi.responses import StreamingResponse, RedirectResponse
from azure.ai.inference.aio import ChatCompletionsClient
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.blob import generate_blob_sas, BlobSasPermissions

from util import get_logger, ChatRequest
from search_index_manager import SearchIndexManager

logger = get_logger(
    name="legal_bot_routes",
    log_level=logging.INFO,
    log_file_name=os.getenv("APP_LOG_FILE"),
    log_to_console=True
)

router = fastapi.APIRouter()


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
    filename format: Case_1107/Case_1107_01_Case_Overview.docx
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
                viewer_url = f"https://view.officeapps.live.com/op/view.aspx?src={quote(blob_url, safe='')}"
                return RedirectResponse(url=viewer_url)

            return RedirectResponse(url=blob_url)

    except Exception as e:
        logger.error(f"Document fetch error: {e}")
        raise fastapi.HTTPException(status_code=500, detail=str(e))


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
                    f"IMPORTANT: At the very end of your response, on a new line, write exactly:\n"
                    f"SOURCES_USED: [comma separated list of document filenames you actually referenced to answer]\n\n"
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

            # Parse SOURCES_USED from completed response
            filtered_citations = citations  # fallback to all if parsing fails
            if "SOURCES_USED:" in accumulated:
                parts = accumulated.split("SOURCES_USED:")
                clean_response = parts[0].strip()
                sources_line = parts[1].strip()
                used_titles = [t.strip() for t in sources_line.split(",")]

                # Filter citations to only docs the LLM actually referenced
                if "SOURCES_USED:" in accumulated:
                    parts = accumulated.split("SOURCES_USED:")
                    clean_response = parts[0].strip()
                    sources_line = parts[1].strip().strip("[]")  # strip brackets
                    used_titles = [t.strip().strip("[]'\"") for t in sources_line.split(",")]

                    filtered_citations = [
                            c for c in citations
                            if any(
                                used.strip() in c["title"] or c["title"] in used.strip()
                                for used in used_titles
                            )
                        ]

                    logger.info(f"Citations available: {[c['title'] for c in citations]}")
                    logger.info(f"LLM used titles raw: {used_titles}")
                    logger.info(f"Filtered to: {[c['title'] for c in filtered_citations]}")

                logger.info(
                    f"LLM used {len(filtered_citations)}/{len(citations)} sources: {used_titles}"
                )

                # Send corrected completed message without the SOURCES_USED line
                yield serialize_sse_event({
                    "content": clean_response,
                    "type": "completed_message"
                })
            else:
                yield serialize_sse_event({
                    "content": accumulated,
                    "type": "completed_message"
                })

            # Stream filtered citations
            if filtered_citations:
                yield serialize_sse_event({
                    "type": "citations",
                    "citations": filtered_citations
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