from flask import Flask, request, jsonify
import json
from datetime import datetime

app = Flask(__name__)
DATA_FILE = "data.json"

def load_data():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.route("/api/device/update", methods=["POST"])
def update_device():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON sent"}), 400

    current_data = load_data()
    current_data.update(data)
    current_data["last_update"] = datetime.now().isoformat()
    save_data(current_data)
    print("✅ Отримані та збережені дані від ESP32:", data)
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    print("✅ Flask сервер запущено!")
    app.run(host="0.0.0.0", port=5000)
