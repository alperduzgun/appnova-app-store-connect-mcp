FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py service.py ./

ENV MCP_TRANSPORT=http
# Default 7860 for Hugging Face Spaces; override with PORT env var for other platforms
ENV PORT=7860

EXPOSE 7860

CMD ["python", "server.py"]
