# Use the official Python 3.10 slim image for a minimal footprint
FROM python:3.10-slim

# Set environment variables for Python and Playwright
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Set the working directory in the container
WORKDIR /app

# Install necessary system dependencies for Playwright (Chromium only)
RUN apt-get update && apt-get install -y --no-install-recommends     libnss3     libnspr4     libatk1.0-0     libatk-bridge2.0-0     libcups2     libdrm2     libxkbcommon0     libxcomposite1     libxdamage1     libxfixes3     libxrandr2     libgbm1     libasound2     libpango-1.0-0     libcairo2     libxshmfence1     curl     && rm -rf /var/lib/apt/lists/*

# Copy the requirements file to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install only the Chromium browser to keep the image lightweight
RUN playwright install chromium 

# Copy the rest of the application code
COPY . .

# Create non-root user for security
RUN useradd -m scraperuser &&     chown -R scraperuser:scraperuser /app /ms-playwright
USER scraperuser

# Create data and downloads directories (these will be mounted)
RUN mkdir -p data/pages downloads

# Expose the port the FastAPI CMS runs on
EXPOSE 8000

# Start the CMS
CMD ["uvicorn", "cms:app", "--host", "0.0.0.0", "--port", "8000"]
