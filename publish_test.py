"""Quick MQTT test publisher -- run this to send telemetry to your thermostat."""

import json
import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883
TOPIC = "home/living_room/thermo_1"

data = {
    "temperature": 23.5,
    "humidity": 45,
    "mode": "cooling",
}

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test-pub")
client.connect(BROKER, PORT)
client.loop_start()

payload = json.dumps(data)
info = client.publish(TOPIC, payload, qos=1)
info.wait_for_publish(timeout=5)

print(f"Published to {TOPIC}: {payload}")
print(f"Result: rc={info.rc}")

client.loop_stop()
client.disconnect()
print("Done.")
