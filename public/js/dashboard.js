// Dashboard bot/app controls. Shared auth guard and navigation live in common.js.
const auth = window.panelAuth;
let backendSessionUid = null;
let apps = [],
  currentProject = "",
  currentFile = "",
  logTimer = null;
async function api(path, options = {}) {
  const r = await fetch("/backend/" + path.replace(/^\//, ""), {
    credentials: "same-origin",
    ...options,
  });
  const raw = await r.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch (_) {
    const detail = raw
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 180);
    throw Error(
      `Backend returned a non-JSON response (HTTP ${r.status}). ${detail || "Check that the selected backend is online and updated."}`,
    );
  }
  if (r.status === 401) {
    await auth.signOut().catch(() => {});
    location.href = "/login";
    throw Error("Your panel session expired. Please log in again.");
  }
  if (!r.ok) throw Error(data.error || "Request failed (" + r.status + ")");
  return { r, data };
}
async function ensureBackendSession(user) {
  if (!user) throw Error("Sign in with Firebase first.");
  if (backendSessionUid === user.uid) return;
  const response = await fetch("/backend/auth/session", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idToken: await user.getIdToken() }),
  });
  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (_) {
    throw Error(
      "Selected bot backend is unavailable or returned a non-JSON response. Firebase login remains active; check the backend URL in the private admin panel.",
    );
  }
  if (!response.ok || !data.ok)
    throw Error(
      data.error || "Could not start a session with the selected bot backend.",
    );
  backendSessionUid = user.uid;
}
function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
async function loadApps() {
  try {
    await ensureBackendSession(auth.currentUser);
    const { data } = await api("api/apps");
    apps = data.apps || [];
    renderApps();
  } catch (e) {
    document.getElementById("apps").innerHTML =
      '<div class="empty error">' +
      esc(e.message) +
      "<br>Confirm the selected backend URL in the private admin panel is correct and online.</div>";
  }
}
function renderApps() {
  const run = apps.filter((a) => a.running).length;
  document.getElementById("total").textContent = apps.length;
  document.getElementById("running").textContent = run;
  document.getElementById("offline").textContent = apps.length - run;
  const q = document.getElementById("search").value.toLowerCase(),
    f = document.getElementById("filter").value;
  const items = apps.filter(
    (a) =>
      (!q || a.name.toLowerCase().includes(q)) &&
      (f === "all" || (f === "running") === a.running),
  );
  document.getElementById("apps").innerHTML = items.length
    ? items
        .map(
          (a) =>
            `<article class="card"><div class="top"><span class="name">${esc(a.name)}</span><span class="pill ${a.running ? "" : "off"}">${a.running ? "RUNNING" : "OFFLINE"}</span></div><div class="meta"><span>${esc(a.device || "—")}</span>${a.running ? `<span>Runs for ${esc(a.remaining_run_min)}m</span>` : ""}</div><div class="toggles"><label><input type="checkbox" ${a.auto_on ? "checked" : ""} onchange="toggle('${esc(a.name)}','auto_on',this)"> Auto-On</label><label><input type="checkbox" ${a.auto_restart ? "checked" : ""} onchange="toggle('${esc(a.name)}','auto_restart',this)"> Auto-Restart</label></div><div class="actions">${a.running ? `<button class="btn danger" onclick="stopApp('${esc(a.name)}')"><i class="fa-solid fa-stop"></i> Stop</button><button class="btn outline" onclick="restartApp('${esc(a.name)}')"><i class="fa-solid fa-rotate-right"></i> Restart</button>` : `<button class="btn" onclick="runApp('${esc(a.name)}')"><i class="fa-solid fa-play"></i> Run</button><button class="btn outline" onclick="validateApp('${esc(a.name)}')"><i class="fa-solid fa-circle-check"></i> Validate</button>`}<button class="btn outline" onclick="showLogs('${esc(a.name)}')"><i class="fa-solid fa-terminal"></i> Logs</button><button class="btn outline" onclick="showFiles('${esc(a.name)}')"><i class="fa-solid fa-folder-open"></i> Files</button><button class="btn outline" onclick="editEnvironment('${esc(a.name)}')"><i class="fa-solid fa-key"></i> Env Vars</button><a class="btn outline" href="/backend/download/${encodeURIComponent(a.name)}"><i class="fa-solid fa-download"></i> Download</a><button class="btn outline" onclick="publicLink('${esc(a.name)}')"><i class="fa-solid fa-link"></i> Public Link</button><button class="btn danger" onclick="deleteApp('${esc(a.name)}')"><i class="fa-solid fa-trash"></i> Delete</button></div></article>`,
        )
        .join("")
    : '<div class="empty">No apps match your search, or no bots have been uploaded yet.</div>';
}
document.getElementById("search").oninput = renderApps;
document.getElementById("filter").onchange = renderApps;
setInterval(loadApps, 60000);
document.getElementById("zip").onchange = (e) => {
  const file = e.target.files[0];
  if (file) upload(file, false);
};
function upload(file, overwrite) {
  const form = new FormData();
  form.append("file", file, file.name);
  if (overwrite) form.append("overwrite", "true");
  const status = document.getElementById("uploadStatus");
  status.textContent = "Uploading…";
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/backend/upload");
  xhr.withCredentials = true;
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable)
      status.textContent =
        "Uploading… " + Math.round((e.loaded / e.total) * 100) + "%";
  };
  xhr.onload = async () => {
    let d = {};
    try {
      d = JSON.parse(xhr.responseText);
    } catch (_) {}
    if (d.needs_confirm) {
      if (confirm(d.error + " Overwrite?")) upload(file, true);
      else status.textContent = "";
      return;
    }
    if (!d.ok) {
      status.textContent = d.error || "Upload failed";
      return;
    }
    status.textContent = "Uploaded " + (d.kind || "project") + ".";
    await loadApps();
  };
  xhr.onerror = () => (status.textContent = "Network error");
  xhr.send(form);
}
async function runApp(n) {
  const card = [...document.querySelectorAll(".card")].find(
    (x) => x.querySelector(".name")?.textContent === n,
  );
  const button = card?.querySelector(".actions .btn");
  if (button) {
    button.disabled = true;
    button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Starting…';
  }
  try {
    const { data } = await api("run/" + encodeURIComponent(n) + "?json=1");
    if (!data.ok) throw Error(data.error || "Could not start the bot");
    await new Promise((resolve) => setTimeout(resolve, 500));
    await loadApps();
    const status = apps.find((a) => a.name === n);
    if (!status?.running)
      throw Error(
        "The process stopped just after launch. Open Logs for the startup error.",
      );
    await Swal.fire({
      title: "Bot is running",
      text: "Started on your selected bot host.",
      icon: "success",
      confirmButtonColor: "#463be0",
    });
  } catch (e) {
    await loadApps();
    Swal.fire({
      title: "Could not start",
      text: e.message,
      icon: "error",
      confirmButtonColor: "#463be0",
    });
  } finally {
    if (button) {
      button.disabled = false;
      button.innerHTML = '<i class="fa-solid fa-play"></i> Run';
    }
  }
}
async function stopApp(n) {
  try {
    await api("stop/" + encodeURIComponent(n) + "?json=1");
    loadApps();
  } catch (e) {
    Swal.fire("Error", e.message, "error");
  }
}
async function restartApp(n) {
  try {
    const { data } = await api("restart/" + encodeURIComponent(n) + "?json=1");
    if (!data.ok) throw Error(data.error || "Restart failed");
    loadApps();
  } catch (e) {
    Swal.fire("Failed", e.message, "error");
  }
}
async function toggle(n, f, el) {
  try {
    const { data } = await api("toggle_" + f + "/" + encodeURIComponent(n), {
      method: "POST",
    });
    if (!data.ok) throw Error("Unable to save setting");
  } catch (e) {
    el.checked = !el.checked;
    Swal.fire("Error", e.message, "error");
  }
}
async function validateApp(n) {
  showModal("Validation · " + n, "Checking…");
  try {
    const { data } = await api("validate/" + encodeURIComponent(n));
    document.getElementById("modalBody").innerHTML = data.ok
      ? "<p>Everything looks good. Entry: " +
        esc(data.entry || "found") +
        "</p>"
      : (data.problems || [])
          .map(
            (p) =>
              '<p class="error"><b>' +
              esc(p.file) +
              "</b> " +
              esc(p.error) +
              "</p>",
          )
          .join("") ||
        '<p class="error">' + esc(data.error || "Validation failed") + "</p>";
  } catch (e) {
    document.getElementById("modalBody").textContent = e.message;
  }
}
function showModal(t, html) {
  document.getElementById("modalTitle").textContent = t;
  document.getElementById("modalBody").innerHTML = html;
  document.getElementById("modal").style.display = "flex";
}
function closeModal() {
  document.getElementById("modal").style.display = "none";
  if (logTimer) clearInterval(logTimer);
}
async function editEnvironment(n) {
  currentProject = n;
  showModal(
    "Environment Variables · " + n,
    '<p class="muted">Values are encrypted at rest and never shown again after saving. Add a name/value pair; for Telegram use <code>TELEGRAM_BOT_TOKEN</code>. Restart this app after changes.</p><div id="envList">Loading…</div><hr style="border-color:var(--line);margin:16px 0"><label class="muted">Variable name</label><input id="envName" autocomplete="off" placeholder="TELEGRAM_BOT_TOKEN" style="display:block;width:100%;margin:6px 0 12px;padding:10px;border-radius:9px;background:#08061b;color:var(--text);border:1px solid var(--line)"><label class="muted">Value (secret)</label><input id="envValue" type="password" autocomplete="new-password" placeholder="Paste secret value" style="display:block;width:100%;margin:6px 0 12px;padding:10px;border-radius:9px;background:#08061b;color:var(--text);border:1px solid var(--line)"><button class="btn" onclick="saveEnvironment()"><i class="fa-solid fa-lock"></i> Save securely</button> <span id="envStatus" class="muted"></span>',
  );
  await refreshEnvironment();
}
async function refreshEnvironment() {
  try {
    const { data } = await api(
      "api/apps/" + encodeURIComponent(currentProject) + "/env",
    );
    const list = document.getElementById("envList");
    if (list)
      list.innerHTML =
        (data.variables || [])
          .map(
            (v) =>
              `<div class="file" style="display:flex;justify-content:space-between;align-items:center"><code>${esc(v)}</code><button class="btn danger small" onclick="deleteEnvironment('${esc(v)}')" title="Delete variable"><i class="fa-solid fa-trash"></i> Remove</button></div>`,
          )
          .join("") ||
        '<p class="muted">No variables saved for this app yet.</p>';
  } catch (e) {
    const list = document.getElementById("envList");
    if (list) list.textContent = e.message;
  }
}
async function saveEnvironment() {
  const name = document.getElementById("envName").value.trim(),
    value = document.getElementById("envValue").value,
    status = document.getElementById("envStatus");
  if (!name || !value) {
    status.textContent = "Enter both a variable name and value.";
    return;
  }
  try {
    const { data } = await api(
      "api/apps/" + encodeURIComponent(currentProject) + "/env",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, value }),
      },
    );
    document.getElementById("envValue").value = "";
    status.textContent =
      data.message || "Saved. Restart the app for it to take effect.";
    await refreshEnvironment();
  } catch (e) {
    status.textContent = e.message;
  }
}
async function deleteEnvironment(name) {
  if (!confirm("Remove " + name + " from this app?")) return;
  try {
    await api(
      "api/apps/" +
        encodeURIComponent(currentProject) +
        "/env?name=" +
        encodeURIComponent(name),
      { method: "DELETE" },
    );
    await refreshEnvironment();
  } catch (e) {
    Swal.fire("Error", e.message, "error");
  }
}
async function showLogs(n) {
  showModal("Logs · " + n, '<pre class="log" id="logContent">Loading…</pre>');
  async function refresh() {
    try {
      const { data } = await api("get_log/" + encodeURIComponent(n));
      const el = document.getElementById("logContent");
      if (el) el.textContent = data.log || "(No logs available yet)";
    } catch (e) {
      const el = document.getElementById("logContent");
      if (el) el.textContent = e.message;
    }
  }
  await refresh();
  if (logTimer) clearInterval(logTimer);
  logTimer = setInterval(refresh, 3000);
}
async function publicLink(n) {
  try {
    const { data } = await api("get_public_link/" + encodeURIComponent(n), {
      method: "POST",
    });
    if (data.ok)
      await Swal.fire({
        title: "Public link",
        html: `<input readonly style="width:100%;padding:10px" value="${esc(data.url)}" onclick="this.select()">`,
      });
    else throw Error(data.error || "Could not create link");
  } catch (e) {
    Swal.fire("Error", e.message, "error");
  }
}
async function deleteApp(n) {
  if (!confirm("Delete " + n + " permanently?")) return;
  try {
    await api("delete/" + encodeURIComponent(n));
    await loadApps();
  } catch (e) {
    Swal.fire("Error", e.message, "error");
  }
}
async function showFiles(n) {
  currentProject = n;
  showModal("Files · " + n, "Loading…");
  try {
    const { data } = await api("list_files/" + encodeURIComponent(n));
    document.getElementById("modalBody").innerHTML =
      (data.files || [])
        .map(
          (f) =>
            `<div class="file" onclick="editFile('${esc(f)}')">${esc(f)}</div>`,
        )
        .join("") || '<p class="muted">No files found.</p>';
  } catch (e) {
    document.getElementById("modalBody").textContent = e.message;
  }
}
async function editFile(f) {
  currentFile = f;
  try {
    const { data } = await api("read_file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: currentProject, filename: f }),
    });
    document.getElementById("modalBody").innerHTML =
      `<p>${esc(f)}</p><textarea id="editor" class="editor">${esc(data.content || "")}</textarea><p><button class="btn" onclick="saveFile()">Save</button> <button class="btn danger" onclick="removeFile()">Delete file</button> <button class="btn outline" onclick="showFiles('${esc(currentProject)}')">Back</button></p>`;
  } catch (e) {
    Swal.fire("Error", e.message, "error");
  }
}
async function saveFile() {
  const content = document.getElementById("editor").value;
  const { data } = await api("save_file", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project: currentProject,
      filename: currentFile,
      content,
    }),
  });
  if (data.status === "success")
    Swal.fire({
      title: "Saved",
      icon: "success",
      timer: 900,
      showConfirmButton: false,
    });
  else Swal.fire("Error", data.error || "Save failed", "error");
}
async function removeFile() {
  if (!confirm("Delete this file?")) return;
  await api("delete_file", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: currentProject, filename: currentFile }),
  });
  showFiles(currentProject);
}
document.getElementById("modal").onclick = (e) => {
  if (e.target.id === "modal") closeModal();
};
window.addEventListener("panel-user-ready", () => loadApps());

document.getElementById("refreshApps").addEventListener("click", loadApps);
document
  .getElementById("chooseZip")
  .addEventListener("click", () => document.getElementById("zip").click());
document.getElementById("modalClose").addEventListener("click", closeModal);
