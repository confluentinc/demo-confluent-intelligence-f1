"""F1 Pit Wall — live web dashboard for workshop attendees.

Consumes the attendee's own Kafka topics (car_telemetry, race_standings,
car_state, pit_decisions) using the keys on their credential card and renders an
animated Silverstone track map, telemetry gauges and a streaming pit-decision
feed in the browser. See ``app.py`` for the entry point (``uv run f1-pitwall``).
"""
