# LegalBot

AI-powered legal case assistant built on Azure AI Foundry, Azure AI Search, and React.

## Prerequisites

- Python 3.11+
- Node 20+
- Docker (optional for containerised dev)
- Azure resources: AI Search, Azure OpenAI, AI Foundry Project, Blob Storage

## Setup

### 1. Environment Variables

```bash
cp .env.example .env
# Fill in your values
```

### 2. Development (no Docker)

**Backend:**
```bash
cd src/api
pip install -r requirements.txt
uvicorn main:app --reload
# Runs on http://localhost:8000
```

**Frontend:**
```bash
cd src/web
npm install
npm run dev
# Runs on http://localhost:5173
# API calls proxied to :8000 via vite.config.ts
```

### 3. Development (Docker)

```bash
docker-compose -f docker-compose.dev.yml up
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000

### 4. Production Build & Push to ACR

```bash
docker build -t insightbotacr.azurecr.io/legal-bot:v1.0.0 .
docker push insightbotacr.azurecr.io/legal-bot:v1.0.0
```

## Project Structure

```
legal-bot/
├── src/
│   ├── api/                  # FastAPI backend
│   │   ├── agents/           # Azure AI Agent setup
│   │   ├── tools/            # Custom search tool (case_id filter)
│   │   ├── routes/           # chat, cases, documents endpoints
│   │   ├── core/             # config, search client
│   │   └── main.py
│   └── web/                  # React + TypeScript frontend
│       └── src/
│           ├── components/   # CaseSelector, ChatWindow
│           ├── hooks/        # useChat
│           └── api/          # fetch client
├── Dockerfile                # prod: single image
├── docker-compose.yml        # prod
└── docker-compose.dev.yml    # dev: split services
```

## How Case Isolation Works

1. User selects a case ID from the dropdown (populated via facet query on the index)
2. Every chat request carries `case_id` to the backend
3. The custom `search_case_documents` tool **always** injects `filter=case_id eq '...'`
4. Azure AI Search only returns chunks from that case — no cross-case bleed is possible
