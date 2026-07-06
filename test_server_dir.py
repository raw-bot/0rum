import urllib.request
import json
print("Waiting for server response...")
req = urllib.request.Request("http://localhost:8787/api/state")
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    print("trades count:", len(data.get("latest_trades", [])))
