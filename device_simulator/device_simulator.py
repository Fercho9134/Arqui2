import json
import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

import paho.mqtt.client as mqtt

load_dotenv()

# =========================
# Configuración
# =========================
MQTT_HOST = os.getenv("MQTT_HOST", "IP_PUBLICA_EC2")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "greenhouse/telemetry")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "greenhouse-simulator-01")

MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

DEVICE_ID = os.getenv("DEVICE_ID", "greenhouse-01")
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "5"))  # segundos

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# =========================
# Logging
# =========================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# =========================
# Estado simulado del invernadero
# =========================
state = {
    "temperature": 25.0,
    "air_humidity": 70.0,
    "soil_moisture": 45.0,
}

running = True


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def update_sensor_values() -> dict:
    global state

    # Variaciones suaves tipo "ambiente real"
    state["temperature"] += random.uniform(-0.4, 0.4)
    state["air_humidity"] += random.uniform(-1.2, 1.2)
    state["soil_moisture"] += random.uniform(-0.8, 0.3)

    state["temperature"] = clamp(state["temperature"], 18.0, 38.0)
    state["air_humidity"] = clamp(state["air_humidity"], 35.0, 95.0)
    state["soil_moisture"] = clamp(state["soil_moisture"], 5.0, 80.0)

    return {
        "device_id": DEVICE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "temperature": round(state["temperature"], 2),
        "air_humidity": round(state["air_humidity"], 2),
        "soil_moisture": round(state["soil_moisture"], 2),
    }


# =========================
# MQTT callbacks
# =========================
def on_connect(client, userdata, connect_flags, reason_code, properties):
    if reason_code == 0:
        logger.info("Conectado a MQTT | broker=%s:%s", MQTT_HOST, MQTT_PORT)
    else:
        logger.error("Error al conectar a MQTT | reason_code=%s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    logger.warning("Desconectado de MQTT | reason_code=%s", reason_code)


def shutdown(client: mqtt.Client):
    global running
    running = False

    logger.info("Deteniendo simulador...")

    try:
        client.loop_stop()
    except Exception:
        pass

    try:
        client.disconnect()
    except Exception:
        pass

    logger.info("Simulador detenido")
    sys.exit(0)


def main():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=MQTT_CLIENT_ID,
        clean_session=True,
    )

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    # Reintentos automáticos si se corta la conexión
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    signal.signal(signal.SIGINT, lambda sig, frame: shutdown(client))
    signal.signal(signal.SIGTERM, lambda sig, frame: shutdown(client))

    logger.info("Conectando al broker MQTT...")
    logger.info("Configuración | host=%s:%s topic=%s client_id=%s", MQTT_HOST, MQTT_PORT, MQTT_TOPIC, MQTT_CLIENT_ID)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    logger.info(
        "Publicando lecturas en %s cada %s segundos | device_id=%s",
        MQTT_TOPIC,
        PUBLISH_INTERVAL,
        DEVICE_ID,
    )

    while running:
        payload = update_sensor_values()
        payload_json = json.dumps(payload)

        result = client.publish(MQTT_TOPIC, payload_json, qos=1)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info("Lectura publicada | %s", payload_json)
        else:
            logger.error("Error publicando mensaje | rc=%s", result.rc)

        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    main()