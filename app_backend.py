import datetime
import json
import os
import socket
import time
import datetime

import boto3
import psycopg2
import redis
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, request

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

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("DB_NAME", "pgv2")
DB_USER = os.environ.get("DB_USER", "pgv2")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_TLS = os.environ.get("REDIS_TLS", "false").lower() == "true"
NOTES_CACHE_KEY = "notes:list"
NOTES_CACHE_TTL_SECONDS = 15

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            ssl=REDIS_TLS,
            socket_connect_timeout=3,
            socket_timeout=3,
            decode_responses=True,
        )
    return _redis_client


@app.get("/health")
def health():
    return jsonify(status="ok", service=SERVICE_NAME, hostname=socket.gethostname())


@app.get("/info")
def info():
    return jsonify(
        service=SERVICE_NAME,
        hostname=socket.gethostname(),
        message="hello from backend-service, reached over the private network-test",
        started_at=datetime.datetime.fromtimestamp(START_TIME, tz=datetime.timezone.utc).isoformat(),
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


@app.get("/db-check")
def db_check():
    start = time.perf_counter()
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=3,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return jsonify(
            success=True,
            service=SERVICE_NAME,
            db_host=DB_HOST,
            db_name=DB_NAME,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
    except psycopg2.OperationalError as exc:
        return jsonify(success=False, service=SERVICE_NAME, db_host=DB_HOST, error=str(exc)), 502


@app.get("/cache-check")
def cache_check():
    start = time.perf_counter()
    try:
        client = _get_redis()
        client.ping()
        probe_key = "cache-check:probe"
        client.set(probe_key, "ok", ex=30)
        client.get(probe_key)
        return jsonify(
            success=True,
            service=SERVICE_NAME,
            redis_host=REDIS_HOST,
            redis_tls=REDIS_TLS,
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
    except redis.RedisError as exc:
        return jsonify(success=False, service=SERVICE_NAME, redis_host=REDIS_HOST, error=str(exc)), 502


def _get_conn():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=3,
    )
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS notes ("
            "id SERIAL PRIMARY KEY, "
            "message TEXT NOT NULL, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    conn.commit()
    return conn


@app.post("/notes")
def create_note():
    message = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not message:
        return jsonify(success=False, service=SERVICE_NAME, error="message is required"), 400
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notes (message) VALUES (%s) RETURNING id, message, created_at",
                    (message,),
                )
                note_id, note_message, created_at = cur.fetchone()
            conn.commit()
        finally:
            conn.close()
        try:
            _get_redis().delete(NOTES_CACHE_KEY)
        except redis.RedisError:
            pass  # cache is best-effort — a stale/unreachable cache must never block a write
        return jsonify(
            success=True,
            service=SERVICE_NAME,
            note={"id": note_id, "message": note_message, "created_at": created_at.isoformat()},
        )
    except psycopg2.Error as exc:
        return jsonify(success=False, service=SERVICE_NAME, error=str(exc)), 502


@app.get("/notes")
def list_notes():
    try:
        cached = _get_redis().get(NOTES_CACHE_KEY)
        if cached is not None:
            return jsonify(success=True, service=SERVICE_NAME, source="cache", notes=json.loads(cached))
    except redis.RedisError:
        pass  # cache miss/unreachable falls through to Postgres below

    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, message, created_at FROM notes ORDER BY id DESC LIMIT 50")
                rows = cur.fetchall()
        finally:
            conn.close()
        notes = [{"id": r[0], "message": r[1], "created_at": r[2].isoformat()} for r in rows]
        try:
            _get_redis().set(NOTES_CACHE_KEY, json.dumps(notes), ex=NOTES_CACHE_TTL_SECONDS)
        except redis.RedisError:
            pass  # cache write failure must never fail the read it's caching
        return jsonify(success=True, service=SERVICE_NAME, source="db", notes=notes)
    except psycopg2.Error as exc:
        return jsonify(success=False, service=SERVICE_NAME, error=str(exc)), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8001)))
