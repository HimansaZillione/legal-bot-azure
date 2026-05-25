import contextlib
import logging
import os
from typing import Union
from urllib.parse import urlparse

import fastapi
from azure.ai.projects.aio import AIProjectClient
from openai import AsyncAzureOpenAI

from azure.ai.inference.aio import ChatCompletionsClient, EmbeddingsClient
from azure.identity import AzureDeveloperCliCredential, ManagedIdentityCredential
from azure.core.credentials import TokenCredential
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from azure.core.credentials import AzureKeyCredential

from search_index_manager import SearchIndexManager
from util import get_logger


logger = None


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    azure_credential: Union[AzureDeveloperCliCredential, ManagedIdentityCredential]

    if not os.getenv("RUNNING_IN_PRODUCTION"):
        if tenant_id := os.getenv("AZURE_TENANT_ID"):
            logger.info("Using AzureDeveloperCliCredential with tenant_id %s", tenant_id)
            azure_credential = AzureDeveloperCliCredential(tenant_id=tenant_id)
        else:
            logger.info("Using AzureDeveloperCliCredential")
            azure_credential = AzureDeveloperCliCredential()
    else:
        user_identity_client_id = os.getenv("AZURE_CLIENT_ID")
        logger.info("Using ManagedIdentityCredential with client_id %s", user_identity_client_id)
        azure_credential = ManagedIdentityCredential(client_id=user_identity_client_id)

    endpoint = os.environ["AZURE_EXISTING_AIPROJECT_ENDPOINT"]
    project = AIProjectClient(
        credential=azure_credential,
        endpoint=endpoint,
    )

    # Inference endpoint derived from project endpoint
    inference_endpoint = f"https://{urlparse(endpoint).netloc}/models"

    chat = ChatCompletionsClient(
        endpoint=inference_endpoint,
        credential=azure_credential,
        credential_scopes=["https://ai.azure.com/.default"],
    )
    embed = AsyncAzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version="2024-02-01"
    )

    # Set up search index manager
    search_endpoint = os.environ.get("AZURE_AI_SEARCH_ENDPOINT")
    search_index_manager = None
    embed_dimensions = None

    if os.getenv("AZURE_AI_EMBED_DIMENSIONS"):
        embed_dimensions = int(os.getenv("AZURE_AI_EMBED_DIMENSIONS"))

    search_credential = AzureKeyCredential(os.environ["AZURE_SEARCH_ADMIN_KEY"])

    if search_endpoint and os.getenv("AZURE_AI_SEARCH_INDEX_NAME"):
        search_index_manager = SearchIndexManager(
            endpoint=search_endpoint,
            credential=search_credential,        # ← key-based, not token-based
            index_name=os.getenv("AZURE_AI_SEARCH_INDEX_NAME"),
            dimensions=embed_dimensions,
            model=os.getenv("AZURE_AI_EMBED_DEPLOYMENT_NAME"),
            embeddings_client=embed,
            semantic_config_name=os.getenv(
                "AZURE_AI_SEARCH_SEMANTIC_CONFIG",
                "hp-test-legaldocs-index-semantic-configuration"
            )
        )
        exists = await search_index_manager.ensure_index_exists()
        if exists:
            logger.info(f"Index '{os.getenv('AZURE_AI_SEARCH_INDEX_NAME')}' confirmed.")
        else:
            logger.error(f"Index '{os.getenv('AZURE_AI_SEARCH_INDEX_NAME')}' NOT FOUND.")
    else:
        logger.warning("Search not configured — RAG will be skipped.")

    app.state.credential = azure_credential
    app.state.chat = chat
    app.state.search_index_manager = search_index_manager
    app.state.chat_model = os.environ["AZURE_AI_CHAT_DEPLOYMENT_NAME"]

    yield

    await project.close()
    await chat.close()
    if search_index_manager:
        await search_index_manager.close()


def create_app():
    if not os.getenv("RUNNING_IN_PRODUCTION"):
        load_dotenv(override=True)

    global logger
    logger = get_logger(
        name="legal_bot",
        log_level=logging.INFO,
        log_file_name=os.getenv("APP_LOG_FILE"),
        log_to_console=True
    )

    app = fastapi.FastAPI(lifespan=lifespan)

    # Serve built frontend in production
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    import routes
    app.include_router(routes.router)

    return app