FROM python:3.12-slim

WORKDIR /app

# Install server dependencies (minimal — no pystray/pillow/esptool for headless)
RUN pip install --no-cache-dir flask>=3.0 qrcode>=7.0

# Copy server code
COPY desktop/shared/ /app/desktop/shared/
COPY tests/ /app/tests/

# Create data directory
RUN mkdir -p /app/desktop/shared/data

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# Default: run the parent server
CMD ["python", "desktop/shared/parent_server.py", "--port", "8080"]
