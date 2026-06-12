const state = {
  doctors: [],
  sanitizers: [],
  logs: [],
  performance: [],
  minimumDuration: 20
};

const els = {
  doctorForm: document.querySelector("#doctorForm"),
  sanitizerForm: document.querySelector("#sanitizerForm"),
  kpiForm: document.querySelector("#kpiForm"),
  refreshLogs: document.querySelector("#refreshLogs"),
  doctorsTable: document.querySelector("#doctorsTable"),
  sanitizersTable: document.querySelector("#sanitizersTable"),
  logsTable: document.querySelector("#logsTable"),
  performanceTable: document.querySelector("#performanceTable"),
  doctorCount: document.querySelector("#doctorCount"),
  sanitizerCount: document.querySelector("#sanitizerCount"),
  logCount: document.querySelector("#logCount"),
  minimumDurationMetric: document.querySelector("#minimumDurationMetric"),
  toast: document.querySelector("#toast")
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadDashboard();
});

function bindEvents() {
  els.doctorForm.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(els.doctorForm);

    await api("/api/doctors", {
      method: "POST",
      body: JSON.stringify({
        doctorName: form.get("doctorName"),
        doctorRFIDTag: form.get("doctorRFIDTag")
      })
    });

    els.doctorForm.reset();
    notify("Doctor saved.");
    await refreshDoctorsAndKpi();
  });

  els.sanitizerForm.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(els.sanitizerForm);

    await api("/api/sanitizers", {
      method: "POST",
      body: JSON.stringify({
        mac: form.get("mac"),
        location: form.get("location")
      })
    });

    els.sanitizerForm.reset();
    notify("Sanitizer saved.");
    await refreshSanitizers();
  });

  els.kpiForm.addEventListener("submit", async event => {
    event.preventDefault();
    const form = new FormData(els.kpiForm);
    const minimumDuration = Number(form.get("minimumDuration"));

    await api("/api/kpi/minimum_duration", {
      method: "PUT",
      body: JSON.stringify({ minimumDuration })
    });

    notify("Minimum duration updated.");
    await refreshKpi();
  });

  els.refreshLogs.addEventListener("click", async () => {
    await refreshLogs();
    notify("Log report refreshed.");
  });
}

async function loadDashboard() {
  try {
    await Promise.all([
      refreshDoctorsAndKpi(),
      refreshSanitizers(),
      refreshLogs()
    ]);
  } catch (error) {
    notify(error.message || "Unable to load dashboard.");
  }
}

async function refreshDoctorsAndKpi() {
  await Promise.all([refreshDoctors(), refreshKpi()]);
}

async function refreshDoctors() {
  state.doctors = await api("/api/doctors");
  els.doctorCount.textContent = state.doctors.length;
  renderDoctors();
}

async function refreshSanitizers() {
  state.sanitizers = await api("/api/sanitizers");
  els.sanitizerCount.textContent = state.sanitizers.length;
  renderSanitizers();
}

async function refreshLogs() {
  state.logs = await api("/api/reports/sanitization_logs");
  els.logCount.textContent = state.logs.length;
  renderLogs();
}

async function refreshKpi() {
  const minimum = await api("/api/kpi/minimum_duration");
  state.minimumDuration = minimum.minimumDuration;
  els.minimumDurationMetric.textContent = state.minimumDuration;
  els.kpiForm.elements.minimumDuration.value = state.minimumDuration;

  state.performance = await api("/api/kpi/doctors");
  renderPerformance();
}

function renderDoctors() {
  if (state.doctors.length === 0) {
    renderEmpty(els.doctorsTable, 3, "No doctors added yet.");
    return;
  }

  els.doctorsTable.innerHTML = state.doctors.map(doctor => `
    <tr>
      <td>${doctor.doctorID}</td>
      <td>${escapeHtml(doctor.doctorName || "Unknown")}</td>
      <td>${escapeHtml(doctor.doctorRFIDTag)}</td>
    </tr>
  `).join("");
}

function renderSanitizers() {
  if (state.sanitizers.length === 0) {
    renderEmpty(els.sanitizersTable, 3, "No sanitizers added yet.");
    return;
  }

  els.sanitizersTable.innerHTML = state.sanitizers.map(sanitizer => `
    <tr>
      <td>${sanitizer.sanitizerID}</td>
      <td>${escapeHtml(sanitizer.mac)}</td>
      <td>${escapeHtml(sanitizer.location || "Unknown")}</td>
    </tr>
  `).join("");
}

function renderLogs() {
  if (state.logs.length === 0) {
    renderEmpty(els.logsTable, 6, "No sanitization logs recorded yet.");
    return;
  }

  els.logsTable.innerHTML = state.logs.map(log => {
    const ok = log.duration >= state.minimumDuration;
    return `
      <tr>
        <td>${formatDate(log.startTime)}</td>
        <td>${escapeHtml(log.doctorName || "Unknown")}</td>
        <td>${escapeHtml(log.doctorRFIDTag)}</td>
        <td>${escapeHtml(log.sanitizerMAC)}</td>
        <td>${escapeHtml(log.location || "Unknown")}</td>
        <td><span class="status ${ok ? "good" : "bad"}">${log.duration}s</span></td>
      </tr>
    `;
  }).join("");
}

function renderPerformance() {
  if (state.performance.length === 0) {
    renderEmpty(els.performanceTable, 7, "No doctor KPI data available.");
    return;
  }

  els.performanceTable.innerHTML = state.performance.map(row => {
    const statusClass = row.totalLogs === 0 ? "warn" : row.compliancePercent >= 80 ? "good" : "bad";

    return `
      <tr>
        <td>${escapeHtml(row.doctorName || "Unknown")}</td>
        <td>${escapeHtml(row.doctorRFIDTag)}</td>
        <td>${row.totalLogs}</td>
        <td>${row.successfulLogs}</td>
        <td>${row.missedLogs}</td>
        <td>${Number(row.averageDuration).toFixed(1)}s</td>
        <td><span class="status ${statusClass}">${Number(row.compliancePercent).toFixed(1)}%</span></td>
      </tr>
    `;
  }).join("");
}

function renderEmpty(target, colspan, message) {
  target.innerHTML = `<tr><td class="empty" colspan="${colspan}">${message}</td></tr>`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });

  if (!response.ok) {
    let message = `Request failed with status ${response.status}.`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch {
      // Keep the HTTP status message when the response is not JSON.
    }
    throw new Error(message);
  }

  return response.json();
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date(value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

let toastTimer;

function notify(message) {
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.add("show");
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 2600);
}
