FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install python-dateutil (used by file_scanner)
RUN pip install --no-cache-dir python-dateutil

COPY . .

RUN mkdir -p /app/data/input

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
