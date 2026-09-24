FROM python:3.10-slim

# Prevent Python from writing .pyc files to disk and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# Install system dependencies required for OpenCV, pyzbar, and camera (v4l2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    v4l-utils \
    libzbar0 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port the app runs on (Default 5050 based on script)
EXPOSE 5050

# Run the server using waitress for production
CMD ["python", "qr_http_server.py", "--host", "0.0.0.0", "--port", "5050", "--use-waitress"]
