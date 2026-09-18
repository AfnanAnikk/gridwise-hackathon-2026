FROM python:3.11-slim

# Install system dependencies including coinor-cbc solver
RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY schemas.py .
COPY interpreter.py .
COPY guardrails.py .
COPY optimizer.py .
COPY main.py .
COPY sample_request.json .

# Expose service port
EXPOSE 8000

# Bind to 0.0.0.0 as required by evaluation rules
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
