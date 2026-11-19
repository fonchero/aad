import json
import urllib.request

BASE = "http://127.0.0.1:8080/api/v1"
REQUEST_ID = "1cbb845a-e3bb-4e79-95da-d382457a0cd3"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        assert response.status == 200
        return json.load(response)

request = get(f"/requests/{REQUEST_ID}")
history = get(f"/requests/{REQUEST_ID}/events")

assert request["status"] == "WAITING_INPUT"

specification = request["specification"]
decision = specification["readiness"]

assert decision["build_readiness"] == "NO_GO"
assert decision["data_feasibility"] == "BLOCKED"

event_types = [e["event_type"] for e in history["events"]]

expected = [
    "REQUEST_CREATED",
    "ANALYSIS_STARTED",
    "REQUIREMENT_ANALYZED",
    "BANKING_DISCOVERY_COMPLETED",
    "READINESS_EVALUATED",
]

assert event_types == expected

assert history["count"] == len(expected)
assert len(decision["questions"]) == 3

print("PASS: Primer circuito integrado de AIF")
print("Estado:", request["status"])
print("Decisión:", decision["build_readiness"])
print("Eventos:", history["count"])
print("Preguntas:", len(decision["questions"]))
