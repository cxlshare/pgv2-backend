import os
import socket
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify

app = Flask(__name__)

SERVICE_NAME = "backend-service"
START_TIME = time.time()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
S3_BUCKET = os.environ.get("S3_BUCKET", "hyventur-demo-images")
IMAGE_KEY = os.environ.get("IMAGE_KEY", "sample.jpg")

# Explicit regional endpoint — boto3's default S3 endpoint resolution can
# produce a presigned URL that fails signature validation for buckets
# outside us-east-1 unless the endpoint is pinned to the bucket's region.
_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
)


@app.get("/health")
def health():
    return jsonify(status="ok", service=SERVICE_NAME, hostname=socket.gethostname())


@app.get("/info")
def info():
    return jsonify(
        service=SERVICE_NAME,
        hostname=socket.gethostname(),
        message="hello from backend-service, reached over the private network",
        uptime_seconds=round(time.time() - START_TIME, 2),
    )


@app.get("/image")
def image():
    try:
        url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": IMAGE_KEY},
            ExpiresIn=300,
        )
        return jsonify(success=True, service=SERVICE_NAME, image_url=url, s3_bucket=S3_BUCKET, image_key=IMAGE_KEY)
    except (BotoCoreError, ClientError) as exc:
        return jsonify(success=False, service=SERVICE_NAME, error=str(exc)), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8001)))
