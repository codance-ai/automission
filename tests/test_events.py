"""Tests for event writing and tailing."""

import json
import time
import threading


from automission.events import EventWriter, EventTailer


class TestEventWriter:
    def test_emit_writes_jsonl(self, tmp_path):
        path = tmp_path / "events.jsonl"
        writer = EventWriter(path)
        writer.emit("mission_started", {"mission_id": "m-abc123", "agents": 2})
        writer.close()

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["type"] == "mission_started"
        assert event["mission_id"] == "m-abc123"
        assert event["agents"] == 2
        assert "ts" in event

    def test_emit_multiple_events(self, tmp_path):
        path = tmp_path / "events.jsonl"
        writer = EventWriter(path)
        writer.emit("attempt_start", {"agent_id": "agent-1", "attempt": 1})
        writer.emit("attempt_end", {"status": "ok", "cost_usd": 0.15})
        writer.close()

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "attempt_start"
        assert json.loads(lines[1])["type"] == "attempt_end"

    def test_emit_flushes_immediately(self, tmp_path):
        path = tmp_path / "events.jsonl"
        writer = EventWriter(path)
        writer.emit("test_event", {})
        # Should be readable without closing
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        writer.close()

    def test_context_manager(self, tmp_path):
        path = tmp_path / "events.jsonl"
        with EventWriter(path) as writer:
            writer.emit("test", {"key": "value"})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_thread_safety(self, tmp_path):
        path = tmp_path / "events.jsonl"
        writer = EventWriter(path)
        errors = []

        def _write_events(n):
            try:
                for i in range(50):
                    writer.emit(f"thread_{n}", {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write_events, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        writer.close()

        assert not errors
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 200  # 4 threads * 50 events
        # Each line should be valid JSON
        for line in lines:
            json.loads(line)


class TestEventTailer:
    def test_tail_existing_events(self, tmp_path):
        path = tmp_path / "events.jsonl"
        with EventWriter(path) as writer:
            writer.emit("event_a", {})
            writer.emit("event_b", {})

        tailer = EventTailer(path)
        events = list(tailer.read_existing())
        assert len(events) == 2
        assert events[0]["type"] == "event_a"
        assert events[1]["type"] == "event_b"

    def test_follow_new_events(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.touch()

        received = []
        stop = threading.Event()

        def _tail():
            tailer = EventTailer(path)
            for event in tailer.follow(stop_event=stop, poll_interval=0.05):
                received.append(event)

        t = threading.Thread(target=_tail, daemon=True)
        t.start()

        time.sleep(0.1)
        with EventWriter(path) as writer:
            writer.emit("live_event", {"data": "hello"})

        time.sleep(0.2)
        stop.set()
        t.join(timeout=1)

        assert len(received) >= 1
        assert received[0]["type"] == "live_event"

    def test_read_existing_empty_file(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.touch()
        tailer = EventTailer(path)
        events = list(tailer.read_existing())
        assert events == []

    def test_follow_stops_on_terminal_event(self, tmp_path):
        path = tmp_path / "events.jsonl"
        with EventWriter(path) as writer:
            writer.emit("attempt_start", {})
            writer.emit("mission_completed", {"total_cost": 1.0})
            writer.emit("should_not_see", {})

        tailer = EventTailer(path)
        events = list(tailer.follow(poll_interval=0.01))
        types = [e["type"] for e in events]
        assert "mission_completed" in types
        assert "should_not_see" not in types

    def test_read_nonexistent_file(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        tailer = EventTailer(path)
        events = list(tailer.read_existing())
        assert events == []
