# 仅打包 API 服务（轻量模式：无 ColPali、无 GPU）。
# 构建：docker build -f deploy/docker/Dockerfile -t rag-agent:latest .
# 运行：见 deploy/compose/docker-compose.yml 或 README 中说明。

FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RAG_ENABLE_REAL_EMBEDDING=false \
    RAG_ENABLE_MULTIMODAL_EMBEDDING=false \
    RAG_ENABLE_COLPALI_RERANK=false \
    RAG_COLPALI_RERANK_API="" \
    RAG_ENABLE_PLAN_EXECUTE_LOOP=false

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY offer_agent ./offer_agent
COPY src ./src
COPY scripts ./scripts
COPY web ./web
COPY data/demo_pages.json ./data/demo_pages.json
COPY main.py .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "offer_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
