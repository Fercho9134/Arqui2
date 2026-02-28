import os
import time
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError


MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://admin:admin@mongo:27017/?authSource=admin",
)
MONGO_DB = os.getenv("MONGO_DB", "iot")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "greenhouse_readings")

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "5000"))

MONGO_CONNECT_RETRIES = int(os.getenv("MONGO_CONNECT_RETRIES", "30"))
MONGO_CONNECT_DELAY = int(os.getenv("MONGO_CONNECT_DELAY", "2"))
MAX_LIMIT = int(os.getenv("MAX_LIMIT", "5000"))

app = Flask(__name__)

_mongo_client = None
_collection = None


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    raw = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def to_iso_z(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def serialize_doc(doc: dict) -> dict:
    metrics = doc.get("metrics", {})

    return {
        "id": str(doc.get("_id")),
        "device_id": doc.get("device_id"),
        "timestamp": to_iso_z(doc.get("timestamp")),
        "temperature": metrics.get("temperature"),
        "air_humidity": metrics.get("air_humidity"),
        "soil_moisture": metrics.get("soil_moisture"),
        "status": doc.get("status"),
        "source": {
            "topic": doc.get("source", {}).get("topic"),
            "ingested_at": to_iso_z(doc.get("source", {}).get("ingested_at")),
        },
    }


def get_collection():
    global _mongo_client, _collection

    if _collection is not None:
        return _collection

    last_error = None

    for attempt in range(1, MONGO_CONNECT_RETRIES + 1):
        try:
            _mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000,
            )
            _mongo_client.admin.command("ping")

            db = _mongo_client[MONGO_DB]
            _collection = db[MONGO_COLLECTION]

            _collection.create_index([("timestamp", -1)])
            return _collection

        except (ServerSelectionTimeoutError, PyMongoError) as exc:
            last_error = exc
            time.sleep(MONGO_CONNECT_DELAY)

    raise RuntimeError(
        f"No se pudo conectar a MongoDB después de {MONGO_CONNECT_RETRIES} intentos"
    ) from last_error


@app.route("/api/readings", methods=["GET"])
def get_readings():
    try:
        start = parse_iso_datetime(request.args.get("start"))
        end = parse_iso_datetime(request.args.get("end"))
    except ValueError:
        return jsonify(
            {
                "error": "Parámetro de fecha inválido",
                "detail": "Usa formato ISO 8601, por ejemplo: 2026-02-28T12:00:00Z",
            }
        ), 400

    if start and end and start > end:
        return jsonify(
            {
                "error": "Rango inválido",
                "detail": "start no puede ser mayor que end",
            }
        ), 400

    limit_raw = request.args.get("limit")
    limit = None

    if limit_raw is not None:
        try:
            limit = int(limit_raw)
        except ValueError:
            return jsonify(
                {
                    "error": "Parámetro inválido",
                    "detail": "limit debe ser un entero positivo",
                }
            ), 400

        if limit <= 0:
            return jsonify(
                {
                    "error": "Parámetro inválido",
                    "detail": "limit debe ser mayor que 0",
                }
            ), 400

        if limit > MAX_LIMIT:
            limit = MAX_LIMIT

    try:
        collection = get_collection()
    except Exception as exc:
        return jsonify(
            {
                "error": "No se pudo conectar a MongoDB",
                "detail": str(exc),
            }
        ), 503

    query = {}
    timestamp_filter = {}

    if start:
        timestamp_filter["$gte"] = start
    if end:
        timestamp_filter["$lte"] = end
    if timestamp_filter:
        query["timestamp"] = timestamp_filter

    # Si hay limit, selecciona los más recientes primero
    cursor = collection.find(query).sort("timestamp", -1)

    limited = False
    if limit is not None:
        cursor = cursor.limit(limit)
        limited = True

    docs = [serialize_doc(doc) for doc in cursor]

    # Si limitó por "más recientes", devolverlos en orden cronológico ayuda al consumo
    if limited:
        docs.reverse()

    return jsonify(
        {
            "count": len(docs),
            "filters": {
                "start": to_iso_z(start),
                "end": to_iso_z(end),
                "limit": limit,
            },
            "data": docs,
        }
    ), 200


if __name__ == "__main__":
    app.run(host=API_HOST, port=API_PORT)