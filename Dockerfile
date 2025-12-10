FROM python:3.12-alpine

# Install git (required for mirroring)
RUN apk add --no-cache git

WORKDIR /app

# Copy requirements first to leverage cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/

# Create directory for mirror data
RUN mkdir -p mirror-data

# Default entrypoint
ENTRYPOINT ["python", "src/g2g.py"]

# Default command (can be overridden)
CMD ["--watch", "--interval", "60"]
