FROM python:3.11-slim

# Install system dependencies (ffmpeg is essential for yt-dlp video/audio merging)
RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg     ca-certificates     curl     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Environment configuration
ENV HEADLESS=1
ENV PORT=5000
EXPOSE 5000

CMD ["python", "server.py", "--no-browser"]
