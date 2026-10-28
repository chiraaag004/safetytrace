# For Hugging Face Spaces (Docker SDK) or any container host. Streamlit Community Cloud does not need this.
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 7860
# Render / Railway / Cloud Run pass the port in $PORT; Hugging Face uses 7860
CMD ["sh", "-c", "python -m uvicorn web.server:app --host 0.0.0.0 --port ${PORT:-7860}"]
