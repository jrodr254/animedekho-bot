FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install N_m3u8DL-RE binary (auto-detect architecture)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
      URL="https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.5.1-beta/N_m3u8DL-RE_v0.5.1-beta_linux-arm64_20251029.tar.gz"; \
    else \
      URL="https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.5.1-beta/N_m3u8DL-RE_v0.5.1-beta_linux-x64_20251029.tar.gz"; \
    fi && \
    curl -L -o /tmp/N_m3u8DL-RE.tar.gz "$URL" && \
    tar -xzf /tmp/N_m3u8DL-RE.tar.gz -C /usr/local/bin/ && \
    rm /tmp/N_m3u8DL-RE.tar.gz && \
    chmod +x /usr/local/bin/N_m3u8DL-RE


# Set working directory
WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Prevent N_m3u8DL-RE Spectre.Console crash in headless containers
ENV TERM=dumb
ENV DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION=0

CMD ["python", "main.py"]
