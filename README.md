# Work Time Tracker

A small employee time tracker built for the Falcon AI programmer test assignment. Add fictional employees, start and stop one timer per employee, and review time by employee and task.

## Run locally

Requires Python 3.11 or newer. The server uses the Python standard library, and the browser UI uses plain JavaScript, HTML and CSS.

```sh
python3 app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On Windows, use `py -3` in place of `python3` if needed. Use `--port 8080` to select a different port or `--db path/to/tracker.sqlite3` to choose another database file. The default is `tracker.sqlite3` in the working directory. Keep the database file when restarting the app.

1. Add a fictional employee, such as Alex Rivera.
2. Enter a task description and select **Start timer**.
3. Select **Stop timer** when the task is finished. To check persistence, stop the server while the timer is running, then start it again and reopen the page.
4. Select a start and end date and refresh the report. The report shows entries and totals for each employee, including zero totals for employees with no entries.

## How it works

- The server records start and stop timestamps in SQLite. After a restart, the UI calculates elapsed time from the original start timestamp, including time while the server was offline.
- A partial unique index enforces one running timer per employee. Starting and stopping use a write transaction, so concurrent requests cannot create two active timers for one person.
- Reports use inclusive UTC calendar dates. The server counts only the portion of an entry inside the selected dates. Running timers are included through the time the report is requested.
- The server listens on loopback. It is designed for local evaluation with fictional data.

## Run the tests

```sh
python3 -m unittest discover -s tests -v
```

The tests use the actual HTTP endpoints, a temporary SQLite database and a controlled clock. They cover employee validation, stopping and starting again, restarting the server with an active timer, empty reports, date boundaries, invalid inputs and simultaneous start requests.

## AI assistance

ChatGPT Codex (GPT-6) generated the first implementation, wrote the tests and used the test results to revise the code. The app itself runs without an AI API or API key.

Example prompts that reproduce the main development steps:

- “Build a dependency-free work time tracker. Use SQLite to persist employees and timestamps. Allow only one running timer per employee.”
- “Write HTTP integration tests that recreate the server against the same SQLite file while a timer is active. Use a controlled clock to verify elapsed time without sleeping.”
- “Test an empty report, a session that crosses midnight, and two simultaneous start requests. Verify that totals count only time inside the selected UTC dates.”
- “Review report input validation. Does the date parser accept formats besides YYYY-MM-DD? Add a failing case and fix the validation.”

### Issue found and fixed

The first implementation relied on Python’s `date.fromisoformat()` to validate the report dates. The integration test showed that a compact date such as `20260104` returned HTTP 200, even though the endpoint describes the format as `YYYY-MM-DD`. The server now compares each parsed date with its canonical ISO string and rejects noncanonical inputs with HTTP 400. The regression case is part of the test suite.
