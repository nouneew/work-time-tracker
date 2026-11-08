const employeeList = document.querySelector("#employees");
const reportView = document.querySelector("#report");
const reportForm = document.querySelector("#report-form");
const statusLine = document.querySelector("#status");
const utcFormatter = new Intl.DateTimeFormat("en", {
  dateStyle: "medium", timeStyle: "medium", timeZone: "UTC",
});

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function showStatus(message, error = false) {
  statusLine.textContent = message;
  statusLine.classList.toggle("error", error);
}

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

function duration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  return [hours, minutes, seconds % 60].map(value => String(value).padStart(2, "0")).join(":");
}

function updateClocks() {
  const now = Math.floor(Date.now() / 1000);
  for (const clock of employeeList.querySelectorAll("[data-started-at]")) {
    clock.textContent = duration(Math.max(0, now - Number(clock.dataset.startedAt)));
  }
}

function handleSubmit(form, action) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const button = form.querySelector("button");
    button.disabled = true;
    showStatus("");
    try {
      await action(new FormData(form));
    } catch (error) {
      showStatus(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
}

async function loadEmployees() {
  const people = await request("/api/employees");
  // Preserve task drafts when another employee's timer changes.
  const drafts = new Map(Array.from(employeeList.querySelectorAll("input[data-employee]"),
    input => [input.dataset.employee, input.value]));
  employeeList.replaceChildren();
  if (!people.length) employeeList.append(node("p", "Add an employee to start tracking time.", "muted"));
  for (const person of people) {
    const card = node("article", undefined, "employee");
    card.append(node("h3", person.name));
    const form = node("form");
    const running = person.started_at !== null;
    if (running) {
      const clock = node("span", undefined, "running");
      clock.dataset.startedAt = person.started_at;
      const details = node("p", person.task + " · ");
      details.append(clock);
      card.append(details);
    } else {
      const label = node("label", "Task description");
      const input = node("input");
      input.name = "task";
      input.required = true;
      input.maxLength = 300;
      input.placeholder = "e.g. Review onboarding flow";
      input.dataset.employee = person.id;
      input.value = drafts.get(String(person.id)) ?? "";
      label.append(input);
      form.append(label);
    }
    form.append(node("button", running ? "Stop timer" : "Start timer"));
    handleSubmit(form, async fields => {
      const endpoint = running ? "/api/stop" : "/api/start";
      const body = { employee_id: person.id };
      if (!running) body.task = fields.get("task");
      await request(endpoint, body);
      await refresh();
      showStatus(running ? "Timer stopped." : "Timer started.");
    });
    card.append(form);
    employeeList.append(card);
  }
  updateClocks();
}

async function loadReport() {
  const params = new URLSearchParams(new FormData(reportForm));
  const people = await request("/api/report?" + params);
  reportView.replaceChildren();
  if (!people.some(person => person.entries.length)) {
    reportView.append(node("p", "No time records for this period.", "muted"));
  }
  for (const person of people) {
    const section = node("article", undefined, "report-person");
    section.append(node("h3", person.name + " · " + duration(person.seconds)));
    if (person.entries.length) {
      const table = node("table");
      const headings = table.createTHead().insertRow();
      for (const label of ["Task", "Started (UTC)", "Stopped (UTC)", "Time in period"]) {
        const heading = node("th", label);
        heading.scope = "col";
        headings.append(heading);
      }
      const body = table.createTBody();
      for (const entry of person.entries) {
        const row = body.insertRow();
        const stopped = entry.stopped_at === null
          ? "Running" : utcFormatter.format(entry.stopped_at * 1000);
        for (const value of [
          entry.task, utcFormatter.format(entry.started_at * 1000), stopped, duration(entry.seconds),
        ]) row.insertCell().textContent = value;
      }
      const wrapper = node("div", undefined, "table-wrap");
      wrapper.append(table);
      section.append(wrapper);
    }
    reportView.append(section);
  }
}

async function refresh() {
  await loadEmployees();
  await loadReport();
}

const today = new Date().toISOString().slice(0, 10);
reportForm.elements.start.value = today.slice(0, 8) + "01";
reportForm.elements.end.value = today;

const addForm = document.querySelector("#add-employee");
handleSubmit(addForm, async fields => {
  await request("/api/employees", { name: fields.get("name") });
  addForm.reset();
  await refresh();
  showStatus("Employee added.");
});
handleSubmit(reportForm, loadReport);
refresh().catch(error => showStatus(error.message, true));
setInterval(updateClocks, 1000);
