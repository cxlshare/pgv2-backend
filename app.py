import os
import socket
import time

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

SERVICE_NAME = "frontend-api"
BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend-service:8001")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET", "hyventur-demo-image")
IMAGE_KEY = os.environ.get("IMAGE_KEY", "sample.jpg")

# Explicit regional endpoint — boto3's default S3 endpoint resolution can
# produce a presigned URL that fails signature validation for buckets
# outside us-east-1 unless the endpoint is pinned to the bucket's region.
_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
)


def get_image_url():
    return _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": IMAGE_KEY},
        ExpiresIn=300,
    )


def check_backend():
    start = time.perf_counter()
    try:
        resp = requests.get(f"{BACKEND_URL}/info", timeout=3)
        resp.raise_for_status()
        return {
            "success": True,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "backend_response": resp.json(),
        }
    except requests.RequestException as exc:
        return {
            "success": False,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "error": str(exc),
        }


def get_backend_image():
    """Ask backend-service for its own presigned S3 URL and relay it —
    backend-service has its own AWS credentials and never gets a host
    port, so this is the only way to see what it fetched."""
    try:
        resp = requests.get(f"{BACKEND_URL}/image", timeout=3)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        return {"success": False, "error": str(exc)}


@app.get("/")
def index():
    image_url = None
    image_error = None
    try:
        image_url = get_image_url()
    except (BotoCoreError, ClientError) as exc:
        image_error = str(exc)

    return render_template(
        "index.html",
        image_url=image_url,
        image_error=image_error,
        s3_bucket=S3_BUCKET,
        image_key=IMAGE_KEY,
        backend_status=check_backend(),
        backend_image=get_backend_image(),
    )


@app.get("/health")
def health():
    return jsonify(status="ok", service=SERVICE_NAME, hostname=socket.gethostname())


@app.get("/test-connection")
def test_connection():
    result = check_backend()
    result["called"] = f"{BACKEND_URL}/info"
    return jsonify(**result), (200 if result["success"] else 502)


@app.get("/load")
def load():
    iterations = int(request.args.get("iterations", 20_000_000))
    start = time.perf_counter()
    total = 0
    for i in range(iterations):
        total += i * i
    elapsed = round(time.perf_counter() - start, 2)
    return jsonify(service=SERVICE_NAME, iterations=iterations, elapsed_seconds=elapsed, checksum=total)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

