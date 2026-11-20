// =====================================================================
//  QueueCare - Patient website (frontend for patients)
//
//  - Talks only to /api/public/... so a patient can NEVER see other
//    patients, notification logs or settings.
//  - Remembers my token in this browser (localStorage) so a page refresh
//    does not lose it.
//  - Pages (Home, Doctors, Get token, My token, Live queue) are shown and
//    hidden with the address hash (#home, #doctors, #book, #token, #queue).
// =====================================================================

let doctors = [];        // public doctor queue info
let myToken = null;      // { no, phone, name, maxAhead } saved in this browser
let myInfo = null;       // the latest info about my token from the server
let doctorSignature = "";
let clockOffset = 0;     // server time minus this device's time (ms), so the clock is the hospital's
let mailNote = "";       // result of the confirmation email, shown under my token
let queueDoctorId = null; // doctor shown on the Live queue page
let currentView = "home";

const VIEWS = ["home", "doctors", "book", "token", "queue"];

// ---------- helpers ----------
function $(selector) {
  return document.querySelector(selector);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, function (letter) {
    const safe = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
    return safe[letter];
  });
}

function icon(name) {
  return `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function showMessage(text, seconds = 3) {
  const box = $("#toast");
  box.textContent = text;
  box.className = "show";
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(function () { box.className = ""; }, seconds * 1000);
}

// "Dr. Sultana" -> "S", "Dr. Md Rahim Khan" -> "MR"
function initials(name) {
  const words = String(name).replace(/^dr\.?\s+/i, "").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) { return "Dr"; }
  return (words[0][0] + (words.length > 1 ? words[1][0] : "")).toUpperCase();
}

// ---------- clock ----------
function nowMs() {
  return Date.now() + clockOffset;
}

function timeText(ms, withSeconds) {
  const options = { hour: "numeric", minute: "2-digit", hour12: true };
  if (withSeconds) {
    options.second = "2-digit";
  }
  return new Date(ms).toLocaleTimeString("en-US", options);
}

function tickClock() {
  const ms = nowMs();
  $("#bclock").textContent = timeText(ms, true);
  $("#hclock").textContent = timeText(ms, false);
  $("#bdate").textContent = new Date(ms).toLocaleDateString("en-GB", {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}

async function callPublic(path, method = "GET", body = null) {
  const options = { method: method, headers: { "Content-Type": "application/json" } };
  if (body) {
    options.body = JSON.stringify(body);
  }
  const response = await fetch("/api/public/" + path, options);
  return response.json();
}

function saveMyToken() {
  try {
    if (myToken) {
      localStorage.setItem("queuecare_token", JSON.stringify(myToken));
    } else {
      localStorage.removeItem("queuecare_token");
    }
  } catch (error) {
    // storage not available: the token just won't survive a refresh
  }
}

function loadMyToken() {
  try {
    const saved = localStorage.getItem("queuecare_token");
    myToken = saved ? JSON.parse(saved) : null;
  } catch (error) {
    myToken = null;
  }
}

// ---------- views (pages) ----------
function showView(name) {
  if (!VIEWS.includes(name)) {
    name = "home";
  }
  currentView = name;
  VIEWS.forEach(function (v) {
    $("#view-" + v).classList.toggle("on", v === name);
  });
  document.querySelectorAll("#sitenav a").forEach(function (link) {
    link.classList.toggle("on", link.dataset.view === name && !link.classList.contains("cta"));
  });
  $("#sitenav").classList.remove("open");
  $("#menubtn").setAttribute("aria-expanded", "false");
  window.scrollTo(0, 0);
  if (name === "book") { drawBookInfo(); }
  drawAll();
}

function chooseDoctor(id) {
  $("#dc").value = String(id);
  location.hash = "#book";
}

// ---------- drawing ----------
function fillDoctorSelect() {
  // The drop-down is rebuilt only when the doctor list changes,
  // so the doctor the patient picked stays selected.
  const signature = doctors.map(function (d) { return d.id + d.name + d.dept; }).join("|");
  if (signature !== doctorSignature) {
    const chosen = $("#dc").value;
    $("#dc").innerHTML = doctors.map(function (d) {
      return `<option value="${d.id}">${escapeHtml(d.name)} - ${escapeHtml(d.dept)}</option>`;
    }).join("");
    if (chosen) {
      $("#dc").value = chosen;
    }
    doctorSignature = signature;
  }
}

function avgOf(d) {
  if (typeof d.avg === "number") { return d.avg; }
  const people = (d.serving ? 1 : 0) + d.waiting;
  return people > 0 ? d.new_wait / people : 8;
}

function drawHome() {
  const rows = doctors.slice(0, 4).map(function (d) {
    return `<div class="live-row">
              <div><b>${escapeHtml(d.name)}</b><span>${escapeHtml(d.dept)} &middot; ${d.waiting} waiting</span></div>
              <div class="num${d.serving ? "" : " none"}">${d.serving ? d.serving : "&ndash;"}</div>
            </div>`;
  }).join("");
  $("#homelive").innerHTML = rows || '<p class="mu" style="margin:10px 0 0">No doctors available yet.</p>';
}

function drawDoctorCards() {
  $("#docgrid").innerHTML = doctors.length === 0
    ? '<p class="mu" style="text-align:center;grid-column:1/-1">No doctors available yet.</p>'
    : doctors.map(function (d) {
        return `<div class="doc">
                  <div class="doc-top"></div>
                  <div class="avatar">${escapeHtml(initials(d.name))}</div>
                  <div class="doc-body">
                    <h3>${escapeHtml(d.name)}</h3>
                    <div><span class="dept">${escapeHtml(d.dept)}</span></div>
                    <div class="doc-stats">
                      <div><b>${d.waiting}</b><span>Waiting</span></div>
                      <div><b>${Math.round(avgOf(d))} min</b><span>Per patient</span></div>
                      <div><b>${d.serving ? d.serving : "&ndash;"}</b><span>Serving</span></div>
                    </div>
                    <button class="pri" onclick="chooseDoctor(${d.id})">Get token</button>
                  </div>
                </div>`;
      }).join("");
}

function drawBookInfo() {
  const d = doctors.find(function (x) { return x.id === Number($("#dc").value); });
  $("#bookinfo").innerHTML = d
    ? `<div class="row"><span>Doctor</span><b>${escapeHtml(d.name)}</b></div>
       <div class="row"><span>Department</span><b>${escapeHtml(d.dept)}</b></div>
       <div class="row"><span>Waiting now</span><b>${d.waiting}</b></div>
       <div class="row"><span>Wait for a new token</span><b>~${d.new_wait} min</b></div>`
    : '<span class="mu">Doctors will appear here.</span>';
}

function drawQueue() {
  if (doctors.length === 0) {
    $("#qtabs").innerHTML = "";
    $("#qbody").innerHTML = '<div class="card"><p class="mu" style="margin:0">No doctors available yet.</p></div>';
    return;
  }
  if (!doctors.some(function (d) { return d.id === queueDoctorId; })) {
    queueDoctorId = doctors[0].id;
  }
  const d = doctors.find(function (x) { return x.id === queueDoctorId; });

  $("#qtabs").innerHTML = doctors.map(function (x) {
    return `<button class="${x.id === queueDoctorId ? "on" : ""}" onclick="pickQueueDoctor(${x.id})">${escapeHtml(x.name)}</button>`;
  }).join("");

  const mineLabel = myInfo && (myInfo.status === "w" || myInfo.status === "s") && myInfo.doctor_id === d.id ? myInfo.label : null;
  const servingMine = mineLabel && d.serving === mineLabel;
  const busy = d.serving ? 1 : 0;
  const avg = avgOf(d);
  const you = function (label) { return label === mineLabel ? ' <span class="you">YOU</span>' : ""; };

  let rows = "";
  if (d.serving) {
    rows += `<tr${d.serving === mineLabel ? ' class="mine"' : ""}><td>${d.serving}${you(d.serving)}</td><td><span class="pill s">In service</span></td><td>Now</td></tr>`;
  }
  d.next.forEach(function (label, index) {
    const em = d.next_emergency[index] ? ' <span class="tag">Priority</span>' : "";
    rows += `<tr${label === mineLabel ? ' class="mine"' : ""}><td>${label}${em}${you(label)}</td><td><span class="pill w">Waiting</span></td><td>~${Math.round((index + busy) * avg)} min</td></tr>`;
  });
  if (d.waiting > d.next.length) {
    rows += `<tr><td colspan="3" class="mu">+ ${d.waiting - d.next.length} more waiting</td></tr>`;
  }
  const table = rows
    ? `<div class="table-wrap"><table><tr><th>Token</th><th>Status</th><th>Waiting time</th></tr>${rows}</table></div>`
    : '<p class="mu" style="margin:0">Nobody is in the queue right now.</p>';

  $("#qbody").innerHTML = `
    <div class="qstats">
      <div class="qstat a${servingMine ? " mine" : ""}">${icon("bell")}<div><span>Currently serving</span><b>${d.serving ? d.serving : "&ndash;"}</b></div></div>
      <div class="qstat b">${icon("users")}<div><span>Waiting patients</span><b>${d.waiting}</b></div></div>
      <div class="qstat c">${icon("clock")}<div><span>Wait for a new patient</span><b>~${d.new_wait} min</b></div></div>
    </div>
    <div class="qgrid">
      <div class="card"><h2>Queue list &middot; ${escapeHtml(d.name)} <span class="mu" style="font-weight:500">(${escapeHtml(d.dept)})</span></h2>${table}</div>
      <div class="calm">
        <svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-logo"/></svg>
        <h3>Stay calm</h3>
        <p>We are taking care of you.</p>
      </div>
    </div>`;
}

function pickQueueDoctor(id) {
  queueDoctorId = id;
  drawQueue();
}

function drawMyToken() {
  const box = $("#tokbox");
  if (!myToken || !myInfo) {
    box.innerHTML = `<div class="card empty">
        <div class="ico">${icon("ticket")}</div>
        <h2>No token yet</h2>
        <p>Get a token to see your live position, or check an existing one below.</p>
        <a class="btn pri" href="#book">Get token</a>
      </div>`;
    return;
  }

  const t = myInfo;
  const waiting = t.status === "w";

  // progress = how many of the people who were ahead of me have been seen
  if (waiting) {
    const before = myToken.maxAhead || 0;
    if (t.ahead > before) {
      myToken.maxAhead = t.ahead;
      saveMyToken();
    }
  }
  const maxAhead = myToken.maxAhead || 0;
  let percent = 100;
  let progressText = "Done";
  if (waiting) {
    percent = maxAhead > 0 ? Math.round(((maxAhead - t.ahead) / maxAhead) * 100) : 100;
    progressText = maxAhead > 0 ? `${maxAhead - t.ahead} / ${maxAhead}` : "Next";
  } else if (t.status === "s") {
    progressText = "Your turn";
  } else if (t.status === "c" || t.status === "k") {
    percent = 0;
    progressText = "Closed";
  }

  let message = "";
  if (waiting) {
    message = `<div class="notice">${icon("check")}Please wait for your turn. You will be told when it is close.</div>`;
  } else if (t.status === "s") {
    message = `<div class="turn-now">It is your turn! Please go to ${escapeHtml(t.doctor_name)} now.</div>`;
  } else if (t.status === "d") {
    message = '<div class="notice">Your consultation is completed. Thank you.</div>';
  } else if (t.status === "c") {
    message = '<div class="notice">This token was cancelled.</div>';
  } else {
    message = '<div class="notice">Your token was skipped. Please contact reception.</div>';
  }

  const greeting = myToken.name ? `Welcome back, ${escapeHtml(myToken.name)}!` : "Your token";
  let html = `
    <div class="welcome">
      <h2>${greeting}</h2>
      <p>${escapeHtml(t.doctor_name)}, ${escapeHtml(t.dept)}${t.emergency ? ' <span class="tag">Priority</span>' : ""}</p>
      <svg class="pulse" viewBox="0 0 330 80" aria-hidden="true"><path d="M0 40h110l16-28 26 58 22-44 14 14h142" fill="none" stroke="#0F766E" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </div>
    <div class="stat3">
      <div class="stat"><div class="ico">${icon("ticket")}</div><div><span>Your token number</span><b>${t.label}</b></div></div>
      <div class="stat"><div class="ico">${icon("users")}</div><div><span>People ahead</span><b>${waiting ? t.ahead : "&ndash;"}</b></div></div>
      <div class="stat"><div class="ico">${icon("clock")}</div><div><span>Estimated wait</span><b>${waiting ? "~" + t.wait + " min" : "&ndash;"}</b>${waiting ? `<small>around ${timeText(t.expected_at, false)}</small>` : ""}</div></div>
    </div>
    <div class="card progress-card">
      <h3><span>Queue progress</span><span class="mu">${progressText}</span></h3>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i style="width:${percent}%"></i></div>
      ${message}`;

  if (mailNote) {
    html += `<p class="mailnote">${escapeHtml(mailNote)}</p>`;
  }
  html += '<div class="token-actions">';
  if (waiting) {
    html += '<button class="danger" onclick="cancelMyToken()">Cancel my token</button>';
  }
  if (t.status === "d" || t.status === "c" || t.status === "k") {
    html += '<button onclick="forgetToken()">Clear</button>';
  }
  html += "</div></div>";
  box.innerHTML = html;
}

function drawAll() {
  drawHome();
  drawDoctorCards();
  drawQueue();
  drawMyToken();
  if (currentView === "book") { drawBookInfo(); }
}

// ---------- data ----------
async function refreshDoctors() {
  try {
    const result = await callPublic("doctors");
    doctors = result.doctors || [];
    if (result.server_time) {
      clockOffset = result.server_time - Date.now();
    }
    fillDoctorSelect();
    drawHome();
    drawDoctorCards();
    drawQueue();
    drawMyToken();
    if (currentView === "book") { drawBookInfo(); }
    const updated = $("#bupd");
    if (updated) { updated.textContent = timeText(nowMs(), true); }
  } catch (error) {
    // server not reachable: keep what is on screen
  }
}

async function refreshMyToken() {
  if (!myToken) {
    drawMyToken();
    return;
  }
  try {
    const result = await callPublic(
      "token?no=" + encodeURIComponent(myToken.no) + "&phone=" + encodeURIComponent(myToken.phone)
    );
    if (result.ok) {
      myInfo = result.token;
    } else {
      myToken = null;
      myInfo = null;
      saveMyToken();
    }
  } catch (error) {
    // keep the old info
  }
  drawMyToken();
  drawQueue();
}

// ---------- buttons ----------
async function takeToken() {
  const name = $("#nm").value.trim();
  const phone = $("#ph").value.trim();
  if (name === "" || phone === "") {
    showMessage("Enter your name and phone number");
    return;
  }

  const result = await callPublic("tokens", "POST", {
    name: name,
    phone: phone,
    email: $("#pe").value.trim(),
    doctor_id: Number($("#dc").value),
  });

  if (result.error) {
    showMessage(result.error);
    return;
  }

  myToken = { no: result.no, phone: phone, name: name, maxAhead: result.token.ahead };
  myInfo = result.token;
  saveMyToken();

  // Tell the patient the REAL result of the confirmation email.
  const typedEmail = $("#pe").value.trim();
  mailNote = "";
  let text = "Your token is " + result.token.label + ". A confirmation SMS is on the way.";
  if (typedEmail && result.email) {
    if (result.email.ok) {
      mailNote = "Confirmation email sent to " + typedEmail + " (check Spam if you do not see it).";
      text = "Your token is " + result.token.label + ". Confirmation email sent to " + typedEmail + ".";
    } else {
      mailNote = "Your token is booked, but the email could not be sent: " + result.email.detail;
      text = mailNote;
    }
  }
  showMessage(text, 7);
  location.hash = "#token";
  drawAll();
  refreshDoctors();
}

async function checkToken() {
  const no = Number($("#tn").value);
  const phone = $("#tp").value.trim();
  if (!no || phone === "") {
    showMessage("Enter the token number and the phone number");
    return;
  }

  const result = await callPublic("token?no=" + no + "&phone=" + encodeURIComponent(phone));
  if (!result.ok) {
    showMessage(result.error || "Token not found");
    return;
  }

  myToken = { no: no, phone: phone, maxAhead: result.token.ahead };
  myInfo = result.token;
  saveMyToken();
  drawMyToken();
  drawQueue();
}

async function cancelMyToken() {
  if (!myToken || !confirm("Cancel your token?")) {
    return;
  }
  const result = await callPublic("tokens/cancel", "POST", { no: myToken.no, phone: myToken.phone });
  if (result.error) {
    showMessage(result.error);
    return;
  }
  showMessage("Token cancelled");
  await refreshMyToken();
  refreshDoctors();
}

function forgetToken() {
  myToken = null;
  myInfo = null;
  mailNote = "";
  saveMyToken();
  drawMyToken();
  drawQueue();
}

// ---------- start ----------
if (location.protocol === "file:") {
  document.body.insertAdjacentHTML("afterbegin",
    '<div style="background:#D1343C;color:#fff;padding:10px 16px">This page only works through the server. ' +
    "Double-click run.bat (or run python app.py) and open the link it shows.</div>");
}

$("#menubtn").addEventListener("click", function () {
  const open = $("#sitenav").classList.toggle("open");
  this.setAttribute("aria-expanded", String(open));
});
$("#dc").addEventListener("change", drawBookInfo);
window.addEventListener("hashchange", function () {
  showView(location.hash.replace("#", ""));
});

tickClock();
setInterval(tickClock, 1000);
loadMyToken();
showView(location.hash.replace("#", ""));
refreshDoctors();
refreshMyToken();

// Live updates every 3 seconds. Only the live parts are redrawn, never the form.
setInterval(function () {
  refreshDoctors();
  refreshMyToken();
}, 3000);
