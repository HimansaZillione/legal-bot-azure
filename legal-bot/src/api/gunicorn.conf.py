import asyncio
import multiprocessing
import os
import pathlib

from dotenv import load_dotenv
from azure.identity.aio import DefaultAzureCredential

load_dotenv(pathlib.Path(__file__).parent.parent.parent / ".env")


async def verify_resources():
    """
    Verify that the Azure AI Search index exists at startup.
    No index creation needed — index is already set up.
    """
    from azure.search.documents.indexes.aio import SearchIndexClient
    from azure.core.exceptions import ResourceNotFoundError

    search_endpoint = os.environ.get("AZURE_AI_SEARCH_ENDPOINT")
    index_name = os.environ.get("AZURE_AI_SEARCH_INDEX_NAME")

    if not search_endpoint or not index_name:
        print("[startup] Search not configured — skipping index check.")
        return

    async with DefaultAzureCredential() as creds:
        async with SearchIndexClient(endpoint=search_endpoint, credential=creds) as ix_client:
            try:
                await ix_client.get_index(index_name)
                print(f"[startup] Index '{index_name}' confirmed.")
            except ResourceNotFoundError:
                print(f"[startup] WARNING: Index '{index_name}' not found.")


def on_starting(server):
    """Server hook, called just before the master process is initialized."""
    asyncio.get_event_loop().run_until_complete(verify_resources())


max_requests = 1000
max_requests_jitter = 50
log_file = "-"
bind = "0.0.0.0:8000"

if not os.getenv("RUNNING_IN_PRODUCTION"):
    reload = True

preload_app = True
num_cpus = multiprocessing.cpu_count()
workers = (num_cpus * 2) + 1
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120

if __name__ == "__main__":
    print("Running verify_resources directly...")
    asyncio.run(verify_resources())
    print("Done.")