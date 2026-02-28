import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

import paho.mqtt.client as mqtt
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError


# =========================
# Configuración por entorno
# =========================
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "greenhouse/telemetry")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "greenhouse-consumer")

MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://admin:admin@mongo:27017/?authSource=admin",
)
MONGO_DB = os.getenv("MONGO_DB", "iot")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "greenhouse_readings")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


MONGO_CONNECT_RETRIES = int(os.getenv("MONGO_CONNECT_RETRIES", "30"))
MONGO_CONNECT_DELAY = int(os.getenv("MONGO_CONNECT_DELAY", "2"))


# =========================
# Logging
# =========================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# =========================
# Mongo
# =========================
mongo_client: MongoClient | None = None
collection: Collection | None = None


def init_mongo() -> Collection:
    global mongo_client

    last_error = None

    for attempt in range(1, MONGO_CONNECT_RETRIES + 1):
        try:
            logger.info(
                "Intentando conectar a MongoDB (%s/%s)...",
                attempt,
                MONGO_CONNECT_RETRIES,
            )

            mongo_client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=5000,
            )

            mongo_client.admin.command("ping")

            db = mongo_client[MONGO_DB]
            col = db[MONGO_COLLECTION]

            col.create_index("timestamp")
            col.create_index([("device_id", 1), ("timestamp", -1)])

            logger.info(
                "Conectado a MongoDB | db=%s | collection=%s",
                MONGO_DB,
                MONGO_COLLECTION,
            )

            return col

        except (ServerSelectionTimeoutError, PyMongoError) as exc:
            last_error = exc
            logger.warning(
                "MongoDB aún no está listo: %s. Reintentando en %s segundos...",
                exc,
                MONGO_CONNECT_DELAY,
            )
            time.sleep(MONGO_CONNECT_DELAY)

    raise RuntimeError(
        f"No se pudo conectar a MongoDB después de {MONGO_CONNECT_RETRIES} intentos"
    ) from last_error


# =========================
# Validación / normalización
# =========================
def parse_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)

    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    raise ValueError("timestamp debe ser string ISO 8601 o venir ausente")


def require_number(payload: Dict[str, Any], key: str) -> float:
    if key not in payload:
        raise ValueError(f"Falta el campo requerido: {key}")

    value = payload[key]

    if not isinstance(value, (int, float)):
        raise ValueError(f"El campo {key} debe ser numérico")

    return float(value)


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:

    if not isinstance(payload, dict):
        raise ValueError("El mensaje debe ser un JSON objeto")

    device_id = payload.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("device_id es requerido y debe ser string no vacío")

    temperature = require_number(payload, "temperature")
    air_humidity = require_number(payload, "air_humidity")
    soil_moisture = require_number(payload, "soil_moisture")


    if not (-20.0 <= temperature <= 80.0):
        raise ValueError("temperature fuera de rango esperado")
    if not (0.0 <= air_humidity <= 100.0):
        raise ValueError("air_humidity fuera de rango esperado")
    if not (0.0 <= soil_moisture <= 100.0):
        raise ValueError("soil_moisture fuera de rango esperado")

    timestamp = parse_timestamp(payload.get("timestamp"))


    status = "normal"
    if temperature > 35 or air_humidity < 30 or soil_moisture < 20:
        status = "warning"
    if temperature > 45 or soil_moisture < 10:
        status = "critical"

    document = {
        "device_id": device_id.strip(),
        "timestamp": timestamp,
        "metrics": {
            "temperature": temperature,
            "air_humidity": air_humidity,
            "soil_moisture": soil_moisture,
        },
        "status": status,
        "source": {
            "topic": MQTT_TOPIC,
            "ingested_at": datetime.now(timezone.utc),
        },
    }

    return document


def on_connect(client, userdata, connect_flags, reason_code, properties):
    if reason_code == 0:
        logger.info("Conectado a MQTT | broker=%s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe(MQTT_TOPIC, qos=1)
        logger.info("Suscrito a tópico: %s", MQTT_TOPIC)
    else:
        logger.error("Fallo al conectar a MQTT | reason_code=%s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    logger.warning("Desconectado de MQTT | reason_code=%s", reason_code)


def on_message(client, userdata, msg):
    global collection

    raw = msg.payload.decode("utf-8", errors="replace")
    logger.debug("Mensaje recibido | topic=%s | payload=%s", msg.topic, raw)

    try:
        payload = json.loads(raw)
        document = normalize_payload(payload)

        if collection is None:
            raise RuntimeError("Mongo collection no inicializada")

        result = collection.insert_one(document)
        logger.info(
            "Lectura almacenada | device_id=%s | status=%s | _id=%s",
            document["device_id"],
            document["status"],
            result.inserted_id,
        )

    except json.JSONDecodeError:
        logger.exception("Payload inválido: no es JSON")
    except Exception as exc:
        logger.exception("Error procesando mensaje: %s", exc)


# =========================
# Shutdown limpio
# =========================
def shutdown(client: mqtt.Client):
    logger.info("Cerrando consumer...")

    try:
        client.loop_stop()
    except Exception:
        pass

    try:
        client.disconnect()
    except Exception:
        pass

    if mongo_client is not None:
        mongo_client.close()

    logger.info("Consumer detenido")
    sys.exit(0)


def main():
    global collection

    collection = init_mongo()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_ID,
        clean_session=True,
    )

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.reconnect_delay_set(min_delay=1, max_delay=30)

    signal.signal(signal.SIGINT, lambda sig, frame: shutdown(client))
    signal.signal(signal.SIGTERM, lambda sig, frame: shutdown(client))

    logger.info("Iniciando consumer...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()