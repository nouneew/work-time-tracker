"""A local time tracker with a SQLite-backed HTTP API."""

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


def now():
    return int(datetime.now(timezone.utc).timestamp())


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(path):
    with closing(connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY,
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                task TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                stopped_at INTEGER,
                CHECK (stopped_at IS NULL OR stopped_at >= started_at)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_running_timer
                ON entries(employee_id) WHERE stopped_at IS NULL;
        """)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def required_text(body, key, limit):
    value = body.get(key)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise ApiError(400, f"{key.capitalize()} must be 1 to {limit} characters.")
    return value.strip()


def employees(db):
    return [dict(row) for row in db.execute("""
        SELECT e.id, e.name, t.task, t.started_at
        FROM employees e
        LEFT JOIN entries t ON t.employee_id = e.id AND t.stopped_at IS NULL
        ORDER BY e.name, e.id
    """)]


def report(db, start_date, end_date, as_of):
    try:
        first, last = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if first.isoformat() != start_date or last.isoformat() != end_date:
            raise ValueError
        start = (first - date(1970, 1, 1)).days * 86400
        end = (last - date(1970, 1, 1)).days * 86400 + 86400
    except ValueError as error:
        raise ApiError(400, "Enter valid dates in YYYY-MM-DD format.") from error
    if first > last:
        raise ApiError(400, "The end date must be on or after the start date.")
    with db:
        db.execute("BEGIN")
        people = {
            person["id"]: {"name": person["name"], "seconds": 0, "entries": []}
            for person in employees(db)
        }
        rows = db.execute("""
            SELECT employee_id, task, started_at, stopped_at
            FROM entries
            WHERE started_at < :end
              AND (COALESCE(stopped_at, :as_of) > :start OR started_at >= :start)
            ORDER BY started_at DESC, id DESC
        """, {"start": start, "end": end, "as_of": as_of})
        for row in rows:
            entry = dict(row)
            stopped = row["stopped_at"]
            effective_end = as_of if stopped is None else stopped
            entry["seconds"] = max(0, min(end, effective_end) - max(start, row["started_at"]))
            person = people[entry.pop("employee_id")]
            person["entries"].append(entry)
            person["seconds"] += entry["seconds"]
    return list(people.values())


def change(db, path, body, clock):
    if path == "/api/employees":
        name = required_text(body, "name", 80)
        try:
            with db:
                employee = db.execute("INSERT INTO employees(name) VALUES (?)", (name,))
                return 201, {"id": employee.lastrowid, "name": name}
        except sqlite3.IntegrityError as error:
            raise ApiError(409, "An employee with that name already exists.") from error

    if path not in ("/api/start", "/api/stop"):
        raise ApiError(404, "Endpoint not found.")
    employee_id = body.get("employee_id")
    if type(employee_id) is not int or not 1 <= employee_id < 2**63:
        raise ApiError(400, "Select a valid employee.")
    with db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
            raise ApiError(404, "Employee not found.")
        timestamp = clock()
        if path == "/api/start":
            task = required_text(body, "task", 300)
            try:
                db.execute(
                    "INSERT INTO entries(employee_id, task, started_at) VALUES (?, ?, ?)",
                    (employee_id, task, timestamp),
                )
            except sqlite3.IntegrityError as error:
                raise ApiError(409, "This employee already has a running timer.") from error
            return 201, {"started_at": timestamp}
        stopped = db.execute("""
            UPDATE entries SET stopped_at = MAX(started_at, ?)
            WHERE employee_id = ? AND stopped_at IS NULL
        """, (timestamp, employee_id))
        if not stopped.rowcount:
            raise ApiError(409, "This employee has no running timer.")
        return 200, {"stopped": True}


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, database, clock, **kwargs):
        self.database = database
        self.clock = clock
        super().__init__(*args, **kwargs)

    def send_payload(self, status, content_type, payload):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status, data):
        self.send_payload(status, "application/json", json.dumps(data).encode())

    def read_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError
        except ValueError as error:
            raise ApiError(400, "Send a JSON object of up to 8 KB.") from error
        return data

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            url = urlsplit(self.path)
            if self.command == "GET" and url.path in ASSETS:
                file, mime = ASSETS[url.path]
                self.send_payload(200, mime, (STATIC / file).read_bytes())
                return
            with closing(connect(self.database)) as db:
                if self.command == "POST":
                    status, data = change(db, url.path, self.read_json(), self.clock)
                elif url.path == "/api/employees":
                    status, data = 200, employees(db)
                elif url.path == "/api/report":
                    params = parse_qs(url.query)
                    start = params.get("start", [""])[0]
                    end = params.get("end", [""])[0]
                    status, data = 200, report(db, start, end, self.clock())
                else:
                    raise ApiError(404, "Endpoint not found.")
            self.send_json(status, data)
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})


def make_server(database, host="127.0.0.1", port=8000, clock=now):
    initialize(database)
    handler = partial(Handler, database=database, clock=clock)
    return ThreadingHTTPServer((host, port), handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("tracker.sqlite3"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with make_server(args.db, port=args.port) as server:
        print(f"Open http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
