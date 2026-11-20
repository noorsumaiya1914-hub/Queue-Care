// =====================================================================
//  QueueCare - the ADMIN panel (frontend, logged-in staff only)
//  Plain JavaScript, no frameworks.
//
//  How it works:
//    - The website only SHOWS data and sends button clicks to the server.
//    - All the queue work (order, wait time, SMS) is done by app.py.
//    - Every 3 seconds the website asks the server for fresh data.
//
//  The file has 6 parts:
//    PART 1  Variables              PART 4  The pages (what you see)
//    PART 2  Small helper functions PART 5  Drawing the page
//    PART 3  Button actions         PART 6  Start
// =====================================================================


// =====================================================================
//  PART 1: VARIABLES (things the website remembers)
// =====================================================================
let loggedInUser = null;       // email of the logged in person (null = nobody)
let currentPage = "dashboard"; // which page is open
let selectedDoctorId = null;   // doctor chosen in the drop-down lists
let myTokenNumber = null;      // token shown in the "Your token" box

// The latest data from the server. It is replaced every 3 seconds.
let data = {
  doctors: [],
  tokens: [],
  stats: { waiting: 0, served: 0, emergencies: 0, sms: 0, emails: 0, online: 0 },
  notifications: [],
  sms_mode: "console",
  email_ready: false,
};

// The menu buttons: [page name, text on the button]
const menuItems = [
  ["dashboard", "Dashboard", "dashboard"],
  ["queue", "Virtual queue and SMS", "ticket"],
  ["priority", "Priority queue", "alert"],
  ["wait", "Predictive wait time", "hourglass"],
  ["board", "Live board", "monitor"],
  ["settings", "Doctors and settings", "sliders"],
];


// =====================================================================
//  PART 2: SMALL HELPER FUNCTIONS
// =====================================================================

// Short name for document.querySelector, e.g. $("#nm") finds id="nm"
function $(selector) {
  return document.querySelector(selector);
}

// Makes text safe to show on the page (stops someone typing HTML code)
function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, function (letter) {
    const safe = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
    return safe[letter];
  });
}

// Shows a small message at the bottom of the screen for 2.6 seconds
function showMessage(text) {
  const box = $("#toast");
  box.textContent = text;
  box.className = "show";
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(function () {
    box.className = "";
  }, 2600);
}

// Talks to the server (app.py). Example: callApi("state") or callApi("tokens", "POST", {...})
async function callApi(path, method = "GET", body = null) {
  const options = { method: method, headers: { "Content-Type": "application/json" } };
  if (body) {
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/api/" + path, options);

  // 401 means "not logged in" (for example the server was restarted):
  // go back to the admin login page
  if (response.status === 401 && path !== "login") {
    loggedInUser = null;
    window.location.href = "/admin";
    throw new Error("not logged in");
  }
  return response.json();
}

// Asks the server for the newest data and keeps it in the variable "data"
async function loadData() {
  try {
    data = await callApi("state");
    // make sure the selected doctor still exists
    const found = data.doctors.some(function (doctor) {
      return doctor.id === selectedDoctorId;
    });
    if (!found) {
      selectedDoctorId = data.doctors.length > 0 ? data.doctors[0].id : null;
    }
  } catch (error) {
    // not logged in or server stopped: keep the old data
  }
}

// Finds one token by its number
function findToken(tokenNumber) {
  return data.tokens.find(function (token) {
    return token.no === tokenNumber;
  });
}

// Builds the <option> lines of a doctor drop-down list
function doctorOptions(selectedId) {
  return data.doctors.map(function (doctor) {
    const selected = doctor.id === selectedId ? " selected" : "";
    return `<option value="${doctor.id}"${selected}>${escapeHtml(doctor.name)} - ${escapeHtml(doctor.dept)}</option>`;
  }).join("");
}

// The title and the grey sentence under it at the top of a page
function pageHeading(title, subtitle) {
  return `<h1>${title}</h1><p class="mu">${subtitle}</p>`;
}

// Small SVG icon from the sprite in admin.html
function icon(name) {
  return `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

// Lets wide tables scroll sideways on small screens
function tableWrap(html) {
  return `<div class="table-wrap">${html}</div>`;
}

// Coloured label for a token status
function statusPill(status) {
  return `<span class="pill ${status}">${statusText(status)}</span>`;
}

// Opens / closes the side menu on phones and tablets
function toggleSide() {
  document.body.classList.toggle("side-open");
}

// Red "Emergency" label
function emergencyTag() {
  return ' <span class="tag">Emergency</span>';
}

// Plain words for a token status
function statusText(status) {
  const words = { w: "Waiting", s: "Being served", d: "Done", k: "Skipped", c: "Cancelled" };
  return words[status] || status;
}

// Small label showing where the token came from
function sourceTag(source) {
  return source === "online"
    ? '<span class="tag on">Online</span>'
    : '<span class="mu">Reception</span>';
}

// =====================================================================
//  PART 3: BUTTON ACTIONS (what happens when you click)
// =====================================================================

// Sends a POST to the server, then reloads the data and redraws the page
async function sendAction(path, body = {}) {
  try {
    const result = await callApi(path, "POST", body);
    if (result.error) {
      showMessage(result.error);
    }
    await loadData();
    drawPage();
    return result;
  } catch (error) {
    return {};
  }
}

function goToPage(pageName) {
  currentPage = pageName;
  document.body.classList.remove("side-open");
  window.scrollTo(0, 0);
  drawPage();
}

async function logout() {
  try {
    await callApi("logout", "POST");
  } catch (error) {
    // already logged out
  }
  window.location.href = "/admin";
}

// A patient (or the reception) takes a token.
// isStaff = true on the Priority page, where the Priority drop-down exists.
async function getToken(isStaff) {
  const name = $("#nm").value.trim();
  const phone = $("#ph").value.trim();
  const email = $("#pe").value.trim();
  if (name === "" || phone === "") {
    showMessage("Enter the name and phone number");
    return;
  }

  const result = await callApi("tokens", "POST", {
    name: name,
    phone: phone,
    email: email,
    doctor_id: Number($("#dc").value),
    emergency: isStaff ? Number($("#pr").value) : 0,
  });
  if (result.error) {
    showMessage(result.error);
    return;
  }

  myTokenNumber = result.no; // show this token in the "Your token" box
  await loadData();
  drawPage();
}

function trackToken() {
  myTokenNumber = Number($("#tr").value) || null;
  drawPage();
}

// Doctor finishes the current patient and calls the next one
async function callNext(doctorId) {
  const result = await sendAction("doctors/" + doctorId + "/next");
  if (result.called === false) {
    showMessage("No patients waiting");
  }
}

// Doctor skips the current patient and calls the next one
async function skipCurrent(doctorId) {
  const result = await sendAction("doctors/" + doctorId + "/skip");
  if (result.called === false) {
    showMessage("No patients waiting");
  }
}

async function addDoctor() {
  const name = $("#dn").value.trim();
  if (name === "") {
    showMessage("Enter the doctor name");
    return;
  }
  await sendAction("doctors", {
    name: name,
    dept: $("#dd").value.trim(),
    avg: Number($("#da").value) || 8,
  });
  showMessage("Doctor added");
}

// Admin: cancel a token (waiting or being served)
async function cancelToken(tokenNumber) {
  const token = findToken(tokenNumber);
  const name = token ? token.name : "this patient";
  if (!confirm("Cancel token for " + name + "?")) {
    return;
  }
  const result = await sendAction("tokens/" + tokenNumber + "/cancel");
  if (result.ok) {
    showMessage("Token cancelled");
  }
}

// Admin: switch a waiting token between Normal and Emergency
async function togglePriority(tokenNumber) {
  const result = await sendAction("tokens/" + tokenNumber + "/priority");
  if (result.ok) {
    showMessage("Priority changed");
  }
}

// "Try the estimate" box on the wait time page: patients ahead x average time
function calculateWait() {
  const doctor = data.doctors.find(function (d) {
    return d.id === Number($("#cd").value);
  });
  const peopleAhead = Number($("#ca").value) || 0;
  const average = doctor ? doctor.avg : 0;
  $("#cr").textContent = Math.round(peopleAhead * average) + " min";
}


// =====================================================================
//  PART 4: THE PAGES (each function returns the HTML of one page)
// =====================================================================

// ---------- the "Your token" box (used on the Virtual queue page) ----------
function myTokenHtml() {
  const token = findToken(myTokenNumber);
  if (!token) {
    return '<p class="mu">Get a token to see your live position.</p>';
  }

  let html = `<div class="big">${token.label}</div>`;
  if (token.status === "w") {
    html += `<p><b>${token.ahead}</b> ahead. Estimated wait: <b>${token.wait} min</b></p>`;
  } else if (token.status === "s") {
    html += `<p><b>It is your turn.</b> Please go to ${escapeHtml(token.doctor_name)}.</p>`;
  } else if (token.status === "d") {
    html += "<p>Consultation completed.</p>";
  } else if (token.status === "c") {
    html += "<p>Token cancelled.</p>";
  } else {
    html += "<p>Token skipped. Please contact reception.</p>";
  }
  html += `<p class="mu">${escapeHtml(token.doctor_name)}, ${escapeHtml(token.dept)}${token.emergency ? emergencyTag() : ""}</p>`;
  return html;
}

// ---------- the list of sent SMS and emails ----------
function notificationLogHtml() {
  if (data.notifications.length === 0) {
    return '<p class="mu">Nothing sent yet.</p>';
  }
  return data.notifications.map(function (item) {
    const state = String(item.status);
    const kind = state.startsWith("Sent.") ? "" : (state.startsWith("Failed.") ? " bad" : " wait");
    return `<div class="sms${kind}"><b>${escapeHtml(item.channel)}</b> ${escapeHtml(item.text)}<br><span class="st">${escapeHtml(item.status)}</span></div>`;
  }).join("");
}

// ---------- charts drawn from the real token data ----------
const chartColors = ["#0F766E", "#12304A", "#2DD4BF", "#F59E0B", "#3B82F6", "#8B5CF6"];

function donutChart(items) {
  const total = items.reduce(function (sum, item) { return sum + item.value; }, 0);
  if (total === 0) {
    return '<p class="mu">No tokens yet.</p>';
  }
  const radius = 42;
  const circle = 2 * Math.PI * radius;
  let used = 0;
  const rings = items.map(function (item, index) {
    const length = (item.value / total) * circle;
    const ring = `<circle cx="60" cy="60" r="${radius}" fill="none" stroke="${chartColors[index % chartColors.length]}" stroke-width="16"
                  stroke-dasharray="${length} ${circle - length}" stroke-dashoffset="${-used}" transform="rotate(-90 60 60)"/>`;
    used += length;
    return ring;
  }).join("");
  const legend = items.map(function (item, index) {
    return `<li><i style="background:${chartColors[index % chartColors.length]}"></i>${escapeHtml(item.label)}<span>${item.value}</span></li>`;
  }).join("");
  return `<div class="donut-wrap">
            <svg class="donut" viewBox="0 0 120 120" role="img" aria-label="Tokens by department">
              <circle cx="60" cy="60" r="${radius}" fill="none" stroke="#E3EEF0" stroke-width="16"/>
              ${rings}
              <text x="60" y="62" text-anchor="middle" font-size="20" font-weight="800">${total}</text>
              <text x="60" y="76" text-anchor="middle" font-size="8" fill="#5B6B7B" style="fill:#5B6B7B">tokens</text>
            </svg>
            <ul class="legend">${legend}</ul>
          </div>`;
}

function departmentChart() {
  const counts = {};
  data.tokens.forEach(function (token) {
    counts[token.dept] = (counts[token.dept] || 0) + 1;
  });
  let items = Object.keys(counts)
    .map(function (dept) { return { label: dept, value: counts[dept] }; })
    .sort(function (a, b) { return b.value - a.value; });
  if (items.length > 6) {
    const rest = items.slice(5).reduce(function (sum, item) { return sum + item.value; }, 0);
    items = items.slice(0, 5).concat([{ label: "Others", value: rest }]);
  }
  return donutChart(items);
}

function doctorLoadChart() {
  if (data.doctors.length === 0) {
    return '<p class="mu">Add a doctor in Settings first.</p>';
  }
  const most = Math.max(1, ...data.doctors.map(function (d) { return d.waiting.length; }));
  return '<div class="hbars">' + data.doctors.map(function (doctor) {
    const percent = Math.round((doctor.waiting.length / most) * 100);
    return `<div class="hbar">
              <div class="top"><b>${escapeHtml(doctor.name)}</b><span>${doctor.waiting.length} waiting &middot; serving ${doctor.serving ? doctor.serving.label : "-"}</span></div>
              <div class="track"><i style="width:${percent}%"></i></div>
            </div>`;
  }).join("") + "</div>";
}

// ---------- DASHBOARD ----------
function dashboardPage() {
  const miniBoxes = [
    [data.stats.emergencies, "Emergencies", "alert", " red"],
    [data.stats.online, "Online tokens", "phone", ""],
    [data.stats.sms, "SMS sent", "sms", ""],
    [data.stats.emails, "Emails sent", "mail", ""],
  ];
  const jumpCards = [
    ["queue", "Virtual queue and SMS", "Patients take a token remotely and get an SMS when their turn is near.", "ticket"],
    ["priority", "Priority queue", "Emergency patients move to the front of the line.", "alert"],
    ["wait", "Predictive wait time", "See how long each patient and doctor queue will take.", "hourglass"],
    ["board", "Live board", "A real-time screen for the waiting area.", "monitor"],
  ];

  const minisHtml = miniBoxes.map(function (box) {
    const ic = box[2];
    return `<div class="mini${box[3]}"><div class="ico">${icon(ic)}</div><div><span>${box[1]}</span><b>${box[0]}</b></div></div>`;
  }).join("");

  const jumpsHtml = jumpCards.map(function (card) {
    return `<button class="jump" onclick="goToPage('${card[0]}')">${icon(card[3])}<b>${card[1]}</b><span>${card[2]}</span></button>`;
  }).join("");

  // the newest tokens that patients booked themselves on the Patient portal
  const online = data.tokens.filter(function (t) { return t.source === "online"; }).slice(-6).reverse();
  const onlineRows = online.map(function (t) {
    return `<tr><td><b>${t.label}</b></td><td>${escapeHtml(t.name)}</td><td>${escapeHtml(t.phone)}</td>
            <td>${escapeHtml(t.doctor_name)}</td><td>${statusPill(t.status)}</td></tr>`;
  }).join("");
  const onlineHtml = online.length > 0
    ? tableWrap(`<table><tr><th>Token</th><th>Patient</th><th>Phone</th><th>Doctor</th><th>Status</th></tr>${onlineRows}</table>`)
    : '<p class="mu">No patient has booked online yet.</p>';

  return pageHeading("Dashboard", "A live overview of every queue.")
    + `<div class="kpis">
         <div class="kpi blue"><div class="ico">${icon("users")}</div><div><span>Waiting now</span><b>${data.stats.waiting}</b></div></div>
         <div class="kpi teal"><div class="ico">${icon("steth")}</div><div><span>Doctors</span><b>${data.doctors.length}</b></div></div>
         <div class="kpi purple"><div class="ico">${icon("check")}</div><div><span>Patients served</span><b>${data.stats.served}</b></div></div>
       </div>
       <div class="minis">${minisHtml}</div>
       <div class="charts">
         <div class="card"><h2>Tokens by department</h2>${departmentChart()}</div>
         <div class="card"><h2>Queue by doctor</h2>${doctorLoadChart()}</div>
       </div>
       <div class="card">
         <h2>Latest online bookings</h2>
         <p class="mu" style="margin-top:0">Tokens that patients took themselves from the Patient portal.</p>
         ${onlineHtml}
       </div>
       <div class="jumps">${jumpsHtml}</div>`;
}

// ---------- FEATURE 1: VIRTUAL QUEUE AND SMS ----------
function queuePage() {
  return pageHeading("Virtual queue and SMS",
      "Take a token without waiting at the counter. An SMS (and an email, if you add one) goes out when 2 or fewer people are ahead.")
    + `<div class="grid">
         <div class="card">
           <h2>Get a token</h2>
           <label for="nm" style="margin-top:0">Name</label>
           <input id="nm" autocomplete="off">
           <label for="ph">Phone number for SMS</label>
           <input id="ph" placeholder="01XXXXXXXXX" inputmode="tel">
           <label for="pe">Email (optional)</label>
           <input id="pe" type="email" placeholder="name@example.com" autocomplete="off">
           <label for="dc">Doctor</label>
           <select id="dc">${doctorOptions(selectedDoctorId)}</select>
           <p style="margin-top:18px"><button class="pri" onclick="getToken(false)">${icon("ticket")}Get token</button></p>
         </div>
         <div class="card">
           <h2>Your token</h2>
           <div id="tokbox">${myTokenHtml()}</div>
           <label for="tr">Track a token by number</label>
           <div class="row-input">
             <input id="tr" type="number" min="1">
             <button onclick="trackToken()">Track</button>
           </div>
         </div>
       </div>
       <div class="card">
         <h2>Notification log</h2>
         <div id="smslog">${notificationLogHtml()}</div>
       </div>`;
}

// ---------- FEATURE 2: PRIORITY QUEUE ----------
function priorityPage() {
  const doctor = data.doctors.find(function (d) {
    return d.id === selectedDoctorId;
  });
  if (!doctor) {
    return pageHeading("Priority queue", "") + '<div class="card"><p>Add a doctor in Settings first.</p></div>';
  }

  const serving = doctor.serving;
  const waitingList = doctor.waiting;

  // one table row for each waiting patient
  let rows = "";
  waitingList.forEach(function (patient, index) {
    const priority = patient.emergency ? '<span class="tag">Emergency</span>' : "Normal";
    rows += `<tr><td>${index + 1}</td><td><b>${patient.label}</b></td><td>${escapeHtml(patient.name)}</td>
             <td>${escapeHtml(patient.phone)}</td><td>${sourceTag(patient.source)}</td>
             <td>${priority}</td><td>${patient.wait} min</td>
             <td><button class="sm" onclick="togglePriority(${patient.no})">${patient.emergency ? "Make normal" : "Make emergency"}</button>
                 <button class="sm danger" onclick="cancelToken(${patient.no})">Cancel</button></td></tr>`;
  });
  const tableHtml = waitingList.length > 0
    ? tableWrap(`<table><tr><th>Position</th><th>Token</th><th>Patient</th><th>Phone</th><th>Source</th><th>Priority</th><th>Estimated wait</th><th>Admin actions</th></tr>${rows}</table>`)
    : '<p class="mu">The queue is empty.</p>';

  return pageHeading("Priority queue", "Add a patient as normal or emergency. Emergency patients are called first.")
    + `<div class="grid">
         <div class="card">
           <h2>Add patient</h2>
           <label for="nm" style="margin-top:0">Name</label>
           <input id="nm" autocomplete="off">
           <label for="ph">Phone number</label>
           <input id="ph" placeholder="01XXXXXXXXX" inputmode="tel">
           <label for="pe">Email (optional)</label>
           <input id="pe" type="email" placeholder="name@example.com" autocomplete="off">
           <label for="dc">Doctor</label>
           <select id="dc">${doctorOptions(doctor.id)}</select>
           <label for="pr">Priority</label>
           <select id="pr"><option value="0">Normal</option><option value="1">Emergency</option></select>
           <p style="margin-top:18px"><button class="pri" onclick="getToken(true)">${icon("plus")}Add to queue</button></p>
         </div>
         <div class="card">
           <label for="sd" style="margin-top:0">Doctor queue</label>
           <select id="sd" onchange="selectedDoctorId = Number(this.value); drawPage()">${doctorOptions(doctor.id)}</select>
           <div class="now">
             <span class="mu">Now serving</span>
             <div class="big">${serving ? serving.label + " " + escapeHtml(serving.name) : "None"}</div>
           </div>
           <button class="pri" onclick="callNext(${doctor.id})">${icon("arrow")}Call next patient</button>
           <button onclick="skipCurrent(${doctor.id})"${serving ? "" : " disabled"}>Skip current</button>
         </div>
       </div>
       <div class="card">
         <h2>Waiting queue (${waitingList.length})</h2>
         ${tableHtml}
       </div>`;
}

// ---------- FEATURE 3: PREDICTIVE WAIT TIME ----------
function waitTimePage() {
  // table with one row for each doctor
  const doctorRows = data.doctors.map(function (doctor) {
    return `<tr><td><b>${escapeHtml(doctor.name)}</b></td><td>${doctor.avg.toFixed(1)} min</td>
            <td>${doctor.serving ? 1 : 0}</td><td>${doctor.waiting.length}</td>
            <td><b>${doctor.new_wait} min</b></td></tr>`;
  }).join("");

  // every waiting patient, shortest wait first
  const waitingTokens = data.tokens
    .filter(function (token) { return token.status === "w"; })
    .sort(function (a, b) { return a.wait - b.wait; });
  const patientRows = waitingTokens.map(function (token) {
    return `<tr><td><b>${token.label}</b></td><td>${escapeHtml(token.name)}</td>
            <td>${escapeHtml(token.doctor_name)}</td><td>${token.wait} min</td></tr>`;
  }).join("");
  const patientsHtml = waitingTokens.length > 0
    ? tableWrap(`<table><tr><th>Token</th><th>Patient</th><th>Doctor</th><th>Wait</th></tr>${patientRows}</table>`)
    : '<p class="mu">Nobody is waiting.</p>';

  const firstDoctor = data.doctors.find(function (d) { return d.id === selectedDoctorId; });
  const startEstimate = Math.round(3 * (firstDoctor ? firstDoctor.avg : 0));

  return pageHeading("Predictive wait time",
      "Wait = (patients ahead, including the one being served) x the doctor's average consultation time.")
    + `<div class="card">
         <h2>Doctors right now</h2>
         ${tableWrap(`<table>
           <tr><th>Doctor</th><th>Average consultation</th><th>Being served</th><th>Waiting</th><th>Wait for a new patient</th></tr>
           ${doctorRows}
         </table>`)}
         <p class="mu" style="margin:12px 0 0">The average updates after each completed consultation.</p>
       </div>
       <div class="grid">
         <div class="card">
           <h2>Try the estimate</h2>
           <label for="cd" style="margin-top:0">Doctor</label>
           <select id="cd" onchange="calculateWait()">${doctorOptions(selectedDoctorId)}</select>
           <label for="ca">Patients ahead</label>
           <input id="ca" type="number" min="0" value="3" oninput="calculateWait()">
           <p class="mu" style="margin:16px 0 0">Estimated wait</p>
           <div class="big" id="cr">${startEstimate} min</div>
         </div>
         <div class="card">
           <h2>Every waiting patient</h2>
           ${patientsHtml}
         </div>
       </div>`;
}

// ---------- FEATURE 4: LIVE BOARD ----------
function boardPage() {
  const doctorCards = data.doctors.map(function (doctor) {
    const nextFour = doctor.waiting.slice(0, 4).map(function (patient) {
      return `<span class="chip${patient.emergency ? " em" : ""}">${patient.label}</span>`;
    }).join("") || '<span class="mu">Nobody waiting</span>';

    return `<div class="card">
              <h2 style="margin-bottom:2px">${escapeHtml(doctor.name)}</h2>
              <p class="mu" style="margin:0">${escapeHtml(doctor.dept)}</p>
              <p class="mu" style="margin:14px 0 0">Now serving</p>
              <div class="huge">${doctor.serving ? doctor.serving.label : "-"}</div>
              <p class="mu" style="margin:10px 0 6px">Next in line</p>
              <div>${nextFour}</div>
              <p class="mu" style="margin:12px 0 0">A new patient would wait about ${doctor.new_wait} min.</p>
            </div>`;
  }).join("");

  return `<div class="live"><i></i>Live queue</div><div class="grid">${doctorCards}</div>`;
}

// ---------- SETTINGS ----------
function settingsPage() {
  const doctorRows = data.doctors.map(function (doctor) {
    return `<tr><td><b>${escapeHtml(doctor.name)}</b></td><td>${escapeHtml(doctor.dept)}</td><td>${doctor.avg.toFixed(1)}</td></tr>`;
  }).join("");

  const smsStatus = data.sms_mode === "console"
    ? '<b class="warn">SMS mode is console, so no real SMS is sent.</b> '
    : "SMS mode: " + escapeHtml(data.sms_mode) + ". ";
  const emailStatus = data.email_ready
    ? "Email is set up."
    : '<b class="warn">Email is not set up.</b>';

  return pageHeading("Doctors and settings", "Set up your doctors and departments.")
    + `<div class="grid">
         <div class="card">
           <h2>Doctors</h2>
           ${tableWrap(`<table><tr><th>Doctor</th><th>Department</th><th>Avg min</th></tr>${doctorRows}</table>`)}
         </div>
         <div class="card">
           <h2>Add a doctor</h2>
           <label for="dn" style="margin-top:0">Doctor name</label>
           <input id="dn">
           <label for="dd">Department</label>
           <input id="dd">
           <label for="da">Average consultation (min)</label>
           <input id="da" type="number" value="8">
           <p style="margin-top:18px"><button class="pri" onclick="addDoctor()">${icon("plus")}Add doctor</button></p>
         </div>
         <div class="card">
           <h2>Notifications</h2>
           <p class="mu" style="margin-top:0">${smsStatus}${emailStatus} Settings live in the .env file (restart the server after changing it).</p>
           <h3 style="margin:16px 0 8px">Latest notifications</h3>
           <div id="tlog">${notificationLogHtml()}</div>
         </div>
         <div class="card">
           <h2>SMS check and test</h2>
           <p class="mu" style="margin-top:0">First check the connection, then send a test SMS to your own phone. The result appears in the notification log.</p>
           <p><button onclick="checkSms()">Check SMS connection</button></p>
           <div id="smscheck"></div>
           <label for="ts">Test phone number</label>
           <input id="ts" placeholder="01XXXXXXXXX" inputmode="tel">
           <p style="margin-top:18px"><button class="pri" onclick="sendTestSms()">Send test SMS</button></p>
         </div>
       </div>`;
}

// Which function draws which page
const pages = {
  dashboard: dashboardPage,
  queue: queuePage,
  priority: priorityPage,
  wait: waitTimePage,
  board: boardPage,
  settings: settingsPage,
};


// =====================================================================
//  PART 5: DRAWING THE PAGE
// =====================================================================
function drawPage() {
  // Nobody logged in: the login page is a separate page (/admin)
  if (!loggedInUser) {
    return;
  }

  // Top right: user name and Log out button
  $("#who").innerHTML = `<span class="av">${escapeHtml(loggedInUser.charAt(0).toUpperCase())}</span><span class="em-addr">${escapeHtml(loggedInUser)}</span><button onclick="logout()">${icon("logout")}Log out</button>`;

  // Menu buttons (the open page is highlighted with class "on")
  $("#nav").innerHTML = menuItems.map(function (item) {
    const pageName = item[0];
    const label = item[1];
    const highlight = pageName === currentPage ? "on" : "";
    return `<button class="${highlight}" onclick="goToPage('${pageName}')">${icon(item[2])}${label}</button>`;
  }).join("");

  // Title in the top bar
  const current = menuItems.find(function (item) { return item[0] === currentPage; });
  $("#crumb").innerHTML = current ? icon(current[2]) + current[1] : "";

  // The page itself
  $("#main").innerHTML = pages[currentPage]();
}


// =====================================================================
//  PART 6: START
// =====================================================================

// If someone double-clicks index.html, the server is not running. Warn them.
if (location.protocol === "file:") {
  document.body.insertAdjacentHTML("afterbegin",
    '<div style="background:#D1343C;color:#fff;padding:10px 16px">This page only works through the server. ' +
    "Double-click run.bat (or run python app.py) and open the link it shows.</div>");
}

// When the page opens: are we already logged in? Then load the data.
async function start() {
  try {
    const me = await callApi("me");
    if (me.ok) {
      loggedInUser = me.email;
      await loadData();
    }
  } catch (error) {
    return; // not logged in: callApi already sent the browser to /admin
  }
  drawPage();
}
start();

// Live updates: every 3 seconds ask the server for new data.
// Pages with typing boxes are not redrawn, so what you type is not lost.
setInterval(async function () {
  if (!loggedInUser) {
    return;
  }
  await loadData();

  if (["dashboard", "wait", "board"].includes(currentPage)) {
    drawPage();
  } else if (currentPage === "settings") {
    if ($("#tlog")) { $("#tlog").innerHTML = notificationLogHtml(); }
  } else if (currentPage === "queue") {
    if ($("#tokbox")) { $("#tokbox").innerHTML = myTokenHtml(); }
    if ($("#smslog")) { $("#smslog").innerHTML = notificationLogHtml(); }
  }
}, 3000);


// ---------- SMS check and test (Settings page) ----------
async function checkSms() {
  $("#smscheck").innerHTML = '<p class="mu">Checking...</p>';
  try {
    const r = await callApi("sms-check");
    $("#smscheck").innerHTML = (r.results || []).map(function (x) {
      return `<div class="sms${x.ok ? "" : " bad"}"><b>${escapeHtml(x.provider)}</b>: ${x.ok ? "Ready." : "Problem."}<br><span class="st">${escapeHtml(x.text)}</span></div>`;
    }).join("");
  } catch (error) {
    $("#smscheck").innerHTML = '<p class="mu">Check failed.</p>';
  }
}

async function sendTestSms() {
  const r = await callApi("test-sms", "POST", { phone: $("#ts").value.trim() });
  showMessage(r.error ? r.error : "Test SMS is being sent. Watch the log below.");
}
