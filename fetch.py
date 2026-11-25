#!/usr/bin/env python3
"""
Henter temperaturer fra Smart Life (Tuya) og lagrer dem som JSON for nettsiden.

To bruksmåter:

  python3 fetch.py --setup
      Kjøres ÉN gang på din egen Mac, i samme mappe som tuya_token.json.
      Lager data/token.enc (kryptert innlogging) og skriver ut en nøkkel
      som du legger inn som hemmeligheten TUYA_KEY på GitHub.

  python3 fetch.py
      Kjøres automatisk av GitHub hvert 15. minutt. Leser nøkkelen fra
      miljøvariabelen TUYA_KEY, henter målinger og oppdaterer
      data/latest.json og data/history.json.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TOKEN_ENC = os.path.join(DATA, "token.enc")
LATEST = os.path.join(DATA, "latest.json")
HISTORY = os.path.join(DATA, "history.json")
HISTORY_DAYS = 7

TEMP_CODES = ["va_temperature", "temp_current", "temp_indoor", "cur_temp", "temperature"]
HUM_CODES = ["va_humidity", "humidity_value", "humidity_indoor", "humidity"]
BATT_CODES = ["battery_percentage", "va_battery", "battery_state"]


# ---------- kryptert lagring av innloggingen ----------

def write_token(data, key):
    os.makedirs(DATA, exist_ok=True)
    blob = Fernet(key).encrypt(json.dumps(data).encode())
    with open(TOKEN_ENC, "wb") as f:
        f.write(blob)


def read_token(key):
    with open(TOKEN_ENC, "rb") as f:
        return json.loads(Fernet(key).decrypt(f.read()))


def make_token_saver(data, key):
    """Tuya fornyer innloggingen jevnlig – lagre den nye, ellers slutter det å virke."""
    try:
        from tuya_sharing import SharingTokenListener
    except ImportError:
        from tuya_sharing.customerapi import SharingTokenListener

    class TokenSaver(SharingTokenListener):
        def update_token(self, token_info):
            data["token"] = token_info
            write_token(data, key)

    return TokenSaver()


# ---------- lesing av verdier ----------

def scaled(device, code, value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    scale = 0
    for src in (getattr(device, "status_range", {}) or {}, getattr(device, "function", {}) or {}):
        item = src.get(code)
        if item is not None:
            try:
                scale = json.loads(item.values).get("scale", 0)
            except Exception:
                pass
            break
    return round(value / (10 ** scale), 2) if scale else value


def first(device, codes):
    for c in codes:
        if c in device.status:
            return scaled(device, c, device.status[c])
    return None


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def dump_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ---------- hovedprogram ----------

def setup():
    src = os.path.join(HERE, "tuya_token.json")
    if not os.path.exists(src):
        sys.exit("Fant ikke tuya_token.json i denne mappen. Kjør tuya_test.py først.")
    key = Fernet.generate_key()
    write_token(json.load(open(src)), key)
    print("\nFerdig! Laget data/token.enc.\n")
    print("Legg inn denne nøkkelen som hemmeligheten TUYA_KEY på GitHub:\n")
    print("   " + key.decode() + "\n")
    print("Ikke del nøkkelen med andre.")


def run():
    key = os.environ.get("TUYA_KEY", "").strip().encode()
    if not key:
        sys.exit("Mangler TUYA_KEY.")
    data = read_token(key)

    from tuya_sharing import Manager
    manager = Manager(
        CLIENT_ID,
        data["user_code"],
        data["terminal_id"],
        data["endpoint"],
        data["token"],
        make_token_saver(data, key),
    )
    manager.update_device_cache()

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    sensors = []
    for d in manager.device_map.values():
        temp = first(d, TEMP_CODES)
        if temp is None:
            continue
        sensors.append({
            "id": d.id,
            "name": d.name,
            "temp": temp,
            "hum": first(d, HUM_CODES),
            "batt": first(d, BATT_CODES),
            "online": bool(d.online),
        })
    sensors.sort(key=lambda s: s["name"].lower())

    os.makedirs(DATA, exist_ok=True)
    dump_json(LATEST, {"updated": now, "sensors": sensors})

    hist = load_json(HISTORY, {"names": {}, "points": []})
    point = {"t": now}
    for s in sensors:
        hist["names"][s["id"]] = s["name"]
        if s["online"]:
            point[s["id"]] = s["temp"]
    hist["points"].append(point)
    cutoff = time.time() - HISTORY_DAYS * 86400
    hist["points"] = [p for p in hist["points"]
                      if datetime.fromisoformat(p["t"]).timestamp() >= cutoff]
    dump_json(HISTORY, hist)

    print(f"{now}: {len(sensors)} målere lagret")
    for s in sensors:
        print(f"  {s['name']}: {s['temp']} °C")


if __name__ == "__main__":
    setup() if "--setup" in sys.argv else run()
