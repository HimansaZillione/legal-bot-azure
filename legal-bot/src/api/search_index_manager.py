from typing import Optional, Union
from azure.core.credentials_async import AsyncTokenCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.models import VectorizedQuery
from azure.core.exceptions import ResourceNotFoundError
from azure.core.credentials import AzureKeyCredential
from openai import AsyncAzureOpenAI
from util import ChatRequest


class SearchIndexManager:

    def __init__(
        self,
        endpoint: str,
        credential: Union[AsyncTokenCredential, AzureKeyCredential],
        index_name: str,
        dimensions: Optional[int],
        model: str,
        embeddings_client: AsyncAzureOpenAI,
        semantic_config_name: str = "hp-test-legaldocs-index-semantic-configuration"
    ) -> None:
        self._dimensions = dimensions
        self._index_name = index_name
        self._embeddings_client = embeddings_client
        self._endpoint = endpoint
        self._credential = credential
        self._model = model
        self._semantic_config_name = semantic_config_name
        self._client = None

    def _get_client(self) -> SearchClient:
        if self._client is None:
            self._client = SearchClient(
                endpoint=self._endpoint,
                index_name=self._index_name,
                credential=self._credential
            )
        return self._client

    async def search(self, message: ChatRequest, case_id: str) -> str:
        """
        Search documents filtered by case_id.
        case_id filter is always injected — never exposed to the caller to override.
        """
        query_text = message.messages[-1].content

        # Embed via AsyncAzureOpenAI
        response = await self._embeddings_client.embeddings.create(
            input=query_text,
            model=self._model
        )
        embedded_question = response.data[0].embedding

        vector_query = VectorizedQuery(
            vector=embedded_question,
            k_nearest_neighbors=50,
            fields="text_vector"
        )

        results = await self._get_client().search(
            search_text=query_text,
            vector_queries=[vector_query],
            filter=f"case_id eq '{case_id}'",
            query_type="semantic",
            semantic_configuration_name=self._semantic_config_name,
            select=["chunk", "title", "case_id"],
            top=5
        )

        chunks = [
            f"[{r['title']}]\n{r['chunk']}" async for r in results
        ]
        return "\n------\n".join(chunks)
    async def search_with_citations(
        self, message: ChatRequest, case_id: str
    ) -> tuple[str, list[dict]]:
        query_text = message.messages[-1].content

        response = await self._embeddings_client.embeddings.create(
            input=query_text,
            model=self._model
        )
        embedded_question = response.data[0].embedding

        vector_query = VectorizedQuery(
            vector=embedded_question,
            k_nearest_neighbors=50,
            fields="text_vector"
        )

        results = await self._get_client().search(
            search_text=query_text,
            vector_queries=[vector_query],
            filter=f"case_id eq '{case_id}'",
            query_type="semantic",
            semantic_configuration_name=self._semantic_config_name,
            select=["chunk", "title", "case_id"],
            top=5
        )

        chunks = []
        citations = []
        seen_titles = set()

        async for r in results:
            title = r["title"]
            case = r["case_id"]
            reranker_score = r.get("@search.reranker_score") or 0

            print(f"[citation debug] title={title}, rerankerScore={reranker_score}")

            chunks.append(f"[{title}]\n{r['chunk']}")

            if title not in seen_titles and reranker_score >= 1.5:
                seen_titles.add(title)
                citations.append({
                    "title": title,
                    "path": f"{case}/{title}"
                })

        return "\n------\n".join(chunks), citations

    async def ensure_index_exists(self) -> bool:
        async with SearchIndexClient(
            endpoint=self._endpoint,
            credential=self._credential
        ) as ix_client:
            try:
                await ix_client.get_index(self._index_name)
                return True
            except ResourceNotFoundError:
                return False

    async def get_all_cases(self) -> list[str]:
        results = await self._get_client().search(
            search_text="*",
            facets=["case_id"],
            top=0
        )
        facets = await results.get_facets()
        if facets and "case_id" in facets:
            return sorted([f["value"] for f in facets["case_id"]])
        return []

    async def close(self):
        if self._client:
            await self._client.close()