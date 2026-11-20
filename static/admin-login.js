// QueueCare - admin login page (the only admin file a visitor can see).
// After a correct login the server sends the real admin panel from /admin.

function $(selector) {
  return document.querySelector(selector);
}

function showMessage(text) {
  const box = $("#toast");
  box.textContent = text;
  box.className = "show";
  clearTimeout(showMessage.timer);
  showMessage.timer = setTimeout(function () { box.className = ""; }, 3000);
}

async function login() {
  $("#lgbtn").disabled = true;
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("#em").value.trim(), password: $("#pw").value }),
    });
    const result = await response.json();
    if (result.ok) {
      window.location.href = "/admin";
      return;
    }
    showMessage(result.error || "Wrong email or password");
  } catch (error) {
    showMessage("Cannot reach the server. Run the Flask app and open the site from it.");
  }
  $("#lgbtn").disabled = false;
}

$("#pw").addEventListener("keydown", function (event) {
  if (event.key === "Enter") {
    login();
  }
});
$("#em").focus();
