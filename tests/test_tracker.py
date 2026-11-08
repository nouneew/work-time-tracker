from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_server


class TrackerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "test.sqlite3"
        self.timestamp = int(datetime(2026, 1, 4, tzinfo=timezone.utc).timestamp())

    @contextmanager
    def running_server(self):
        with make_server(self.database, port=0, clock=lambda: self.timestamp) as server:
            self.base = f"http://127.0.0.1:{server.server_port}"
            worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            worker.start()
            try:
                yield
            finally:
                server.shutdown()
                worker.join()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def employee(self, name="Alex Rivera"):
        status, person = self.request("/api/employees", {"name": name})
        self.assertEqual(status, 201)
        return person["id"]

    def report(self, start="2026-01-04", end="2026-01-04"):
        return self.request(f"/api/report?start={start}&end={end}")

    def start(self, employee_id, task="Review onboarding flow"):
        return self.request("/api/start", {"employee_id": employee_id, "task": task})

    def stop(self, employee_id):
        return self.request("/api/stop", {"employee_id": employee_id})

    def test_add_employee_and_reject_duplicate_or_blank_name(self):
        with self.running_server():
            employee_id = self.employee("  Alex Rivera  ")
            self.assertEqual(self.request("/api/employees", {"name": "alex rivera"})[0], 409)
            self.assertEqual(self.request("/api/employees", {"name": "  "})[0], 400)
            self.assertEqual(self.request("/api/employees"), (200, [
                {"id": employee_id, "name": "Alex Rivera", "task": None, "started_at": None}
            ]))

    def test_stop_and_start_again(self):
        with self.running_server():
            person = self.employee()
            self.assertEqual(self.start(person, "  Review mockups  ")[0], 201)
            self.timestamp += 90
            self.assertEqual(self.stop(person)[0], 200)
            self.assertEqual(self.stop(person)[0], 409)
            self.assertEqual(self.start(person, "Fix layout")[0], 201)
            self.timestamp += 30
            self.assertEqual(self.stop(person)[0], 200)
            status, report = self.report()
            self.assertEqual(status, 200)
            self.assertEqual(report[0]["seconds"], 120)
            self.assertEqual([entry["task"] for entry in report[0]["entries"]],
                             ["Fix layout", "Review mockups"])

    def test_active_timer_and_records_survive_server_restart(self):
        with self.running_server():
            person = self.employee()
            started_at = self.timestamp
            self.assertEqual(self.start(person)[0], 201)
        self.timestamp += 300
        with self.running_server():
            active = self.request("/api/employees")[1][0]
            self.assertEqual(active["started_at"], started_at)
            self.assertEqual(active["task"], "Review onboarding flow")
            self.assertEqual(self.start(person)[0], 409)
            active_report = self.report()[1][0]
            self.assertIsNone(active_report["entries"][0]["stopped_at"])
            self.assertEqual(active_report["seconds"], 300)
            self.assertEqual(self.stop(person)[0], 200)
        with self.running_server():
            self.assertIsNone(self.request("/api/employees")[1][0]["started_at"])
            self.assertEqual(self.report()[1][0]["seconds"], 300)

    def test_empty_report_with_and_without_employees(self):
        with self.running_server():
            self.assertEqual(self.report(), (200, []))
            person = self.employee()
            self.start(person)
            self.timestamp += 60
            self.stop(person)
            self.assertEqual(self.report("2026-01-05", "2026-01-05"), (200, [
                {"name": "Alex Rivera", "seconds": 0, "entries": []}
            ]))

    def test_report_clips_at_midnight_and_groups_by_employee(self):
        with self.running_server():
            alex, jamie = self.employee(), self.employee("Jamie Park")
            self.timestamp -= 60
            self.start(alex)
            self.timestamp += 90
            self.start(jamie)
            self.timestamp += 90
            self.stop(alex)
            self.stop(jamie)
            self.assertEqual([person["seconds"] for person in self.report()[1]], [120, 90])
            previous_day = self.report("2026-01-03", "2026-01-03")[1]
            self.assertEqual([person["seconds"] for person in previous_day], [60, 0])

    def test_invalid_dates_and_unknown_employee(self):
        with self.running_server():
            self.assertEqual(self.report("2026-01-05")[0], 400)
            self.assertEqual(self.report("2026-02-30")[0], 400)
            self.assertEqual(self.report("20260104")[0], 400)
            self.assertEqual(self.start(99)[0], 404)

    def test_simultaneous_start_requests_allow_only_one_timer(self):
        with self.running_server():
            employee_id = self.employee()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(self.start, [employee_id, employee_id]))
            self.assertEqual(sorted(status for status, _ in results), [201, 409])


if __name__ == "__main__":
    unittest.main()
