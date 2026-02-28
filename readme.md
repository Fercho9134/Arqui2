# Ejemplo IoT: Invernadero simulado con MQTT, MongoDB, API REST y Grafana

Este proyecto implementa una arquitectura IoT básica desplegada en una instancia **EC2** de AWS.

## Arquitectura

**PC local (dispositivo simulado)**
→ publica lecturas por **MQTT**
→ **Mosquitto** recibe los mensajes
→ **Consumer** procesa y almacena en **MongoDB**
→ **API REST** consulta MongoDB
→ **Grafana** muestra:

* **vista en tiempo real** desde MQTT
* **vista histórica** desde la API REST

## Componentes

* **Mosquitto**: broker MQTT
* **Consumer**: suscriptor MQTT que valida y guarda datos en MongoDB
* **MongoDB**: almacenamiento de lecturas
* **API Flask**: expone un endpoint para consultar lecturas históricas
* **Grafana**: dashboards en tiempo real e históricos
* **Simulador local**: script Python que emula un sensor de invernadero

---

# 1. Crear la instancia EC2

Crea una instancia EC2 con una imagen Ubuntu Server.

## Configuración sugerida

* **AMI**: Ubuntu Server LTS
* **Tipo**: `t3.small` o similar
* **Storage**: 8 GB o más

## Security Group

Habilita estas reglas de entrada:

* **22/TCP** → SSH
* **1883/TCP** → MQTT
* **3000/TCP** → Grafana

Opcional:

* **5000/TCP** → Para probar api desde fuera

---

# 2. Conectarte por SSH

Desde tu PC:

```bash
chmod 400 tu-llave.pem
ssh -i tu-llave.pem ubuntu@TU_IP_PUBLICA_EC2
```

---

# 3. Actualizar el sistema

Ya dentro de la EC2:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
```

---

# 4. Instalar Docker y Docker Compose

Ejecuta:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## Verificar instalación

```bash
docker --version
docker compose version
```

## Usar Docker sin `sudo` (opcional)

```bash
sudo usermod -aG docker $USER
newgrp docker
```

---

# 5. Clonar el repositorio

```bash
git clone https://github.com/Fercho9134/Arqui2
cd Arqui2
```

---

# 6. Verificar plugins de Grafana en `docker-compose.yml`

El servicio `grafana` debe tener preinstalados estos plugins:

* `grafana-mqtt-datasource`
* `yesoreyeram-infinity-datasource`

Ejemplo:

```yaml
grafana:
  image: grafana/grafana-enterprise:12.1
  container_name: iot-grafana
  restart: unless-stopped
  ports:
    - "3000:3000"
  environment:
    GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USER:-admin}
    GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-cambia_esto_ya}
    GF_PLUGINS_PREINSTALL_SYNC: grafana-mqtt-datasource,yesoreyeram-infinity-datasource
  volumes:
    - grafana_data:/var/lib/grafana
  depends_on:
    - mosquitto
    - mongo
    - api
  networks:
    - iot_net
```

---

# 7. Levantar el stack

Desde la raíz del proyecto:

```bash
docker compose up -d --build
```

## Verificar contenedores

```bash
docker compose ps
```

Deberías ver:

* `iot-mosquitto`
* `iot-consumer`
* `iot-mongo`
* `iot-api`
* `iot-grafana`

## Ver logs

```bash
docker compose logs -f
```

---

# 8. Obtener la IP pública de la EC2

Puedes verla desde la consola de AWS o con:

```bash
curl ifconfig.me
```

Guarda esa IP; la usarás en el simulador.

---

# 9. Configurar el simulador local

En tu PC local, actualiza el archivo `.env` del simulador con la IP pública de la EC2.

## Ejemplo `.env`

```env
MQTT_HOST=TU_IP_PUBLICA_EC2
MQTT_PORT=1883
MQTT_TOPIC=greenhouse/telemetry
DEVICE_ID=greenhouse-01
PUBLISH_INTERVAL=5
```

---

# 10. Ejecutar el simulador

Instala dependencias:

```bash
pip install -r requirements.txt
```

```bash
python3 device_simulator.py
```

Si todo está bien, el simulador empezará a publicar lecturas cada 5 segundos.

---

# 11. Verificar que llegan datos

En la EC2, revisa el consumer:

```bash
docker compose logs -f consumer
```

Debes ver mensajes similares a:

* conexión a MongoDB
* conexión a MQTT
* suscripción al tópico
* lecturas almacenadas

---

# 12. Acceder a Grafana

Abre en tu navegador:

```text
http://TU_IP_PUBLICA_EC2:3000
```

Inicia sesión con las credenciales configuradas en tu `docker-compose.yml`.

> La primera vez, los plugins pueden tardar un poco en instalarse. Si no aparecen de inmediato, espera unos segundos y recarga.

---

# 13. Configurar datasource MQTT (tiempo real)

## Crear la conexión

1. En Grafana, ve a **Connections**
2. Haz clic en **Add new connection**
3. Busca **MQTT**
4. Selecciónalo
5. Configura:

* **Name**: `MQTT Live`
* **URI**: `tcp://mosquitto:1883`
* **Client ID**: `grafana-live`

Si Mosquitto está con acceso anónimo, deja usuario y contraseña vacíos.

6. Haz clic en **Save & test**

## Crear panel en tiempo real

1. Ve a **Dashboards**
2. Crea un dashboard nuevo
3. Agrega un panel
4. Selecciona el datasource **MQTT Live**
5. En el query, usa el tópico:

```text
greenhouse/telemetry
```

## Transformaciones recomendadas

Como el payload llega en JSON, usa **Extract fields** y extrae:

* `temperature`
* `air_humidity`
* `soil_moisture`

Con eso puedes crear paneles en tiempo real para cada variable.

---

# 14. Configurar datasource API (históricos)

La API Flask expone un único endpoint:

```text
GET /api/readings
```

Permite:

* filtrar por rango de fechas (`start`, `end`)
* limitar cantidad (`limit`)
* si se usa `limit`, devuelve los datos más recientes

## Crear la conexión en Grafana

1. Ve a **Connections**
2. Haz clic en **Add new connection**
3. Busca **Infinity**
4. Selecciónalo
5. Configura la URL base como:

```text
http://api:5000
```

6. Guarda la conexión

> Como Grafana y la API están en la misma red Docker, se usa `api` como hostname interno.

---

# 15. Crear panel histórico desde la API

## Consulta base

En un panel nuevo:

* **Datasource**: Infinity
* **Type**: `JSON`
* **URL**:

```text
http://api:5000/api/readings?limit=200
```

## Root field

Como la respuesta de la API tiene esta estructura:

```json
{
  "count": 200,
  "filters": { ... },
  "data": [ ... ]
}
```

Debes indicar:

* **Rows / Root field**: `data`

## Campos a usar

Cada registro trae:

* `timestamp`
* `temperature`
* `air_humidity`
* `soil_moisture`
* `status`

Usa `timestamp` como campo de tiempo y luego grafica las métricas que necesites.

---

# 16. Ejemplos del endpoint API

## Últimos 50 datos

```text
http://TU_IP_PUBLICA_EC2:5000/api/readings?limit=50
```

## Datos dentro de un rango

```text
http://TU_IP_PUBLICA_EC2:5000/api/readings?start=2026-02-28T00:00:00Z&end=2026-02-28T23:59:59Z
```

## Datos dentro de un rango con límite

```text
http://TU_IP_PUBLICA_EC2:5000/api/readings?start=2026-02-28T00:00:00Z&end=2026-02-28T23:59:59Z&limit=100
```

---

# 17. Flujo esperado

Cuando todo está funcionando:

1. El simulador publica lecturas MQTT
2. Mosquitto recibe los mensajes
3. El consumer guarda las lecturas en MongoDB
4. Grafana muestra:

   * datos en vivo desde MQTT
   * datos históricos desde la API REST

---

# 18. Detener el proyecto

## Detener simulador local

Presiona `Ctrl + C`

## Detener contenedores en EC2

```bash
docker compose down
```

