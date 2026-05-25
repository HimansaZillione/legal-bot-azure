# Stage 1: Build frontend
FROM node:20-slim AS frontend-build
WORKDIR /app
COPY src/web/package.json ./
RUN npm install
COPY src/web/ ./
RUN npm run build

# Stage 2: Backend + built frontend
FROM python:3.11-slim
WORKDIR /app

COPY src/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/api/ ./

# Copy built frontend into FastAPI static directory
COPY --from=frontend-build /app/dist ./static

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
