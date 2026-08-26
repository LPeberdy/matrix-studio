// Matrix Studio ingress UI. Plain DOM, no build step, no dependencies.
// All URLs are relative so the page works under Home Assistant's ingress
// prefix (set via <base href> by web.py) and standalone.

const PREVIEW_FPS = 8;
const STATUS_INTERVAL_MS = 1000;

const el = (id) => document.getElementById(id);
let sceneOptionsKey = "";
let brightnessPending = false;

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) {
    console.error("request failed", path, response.status);
  }
  return response.json().catch(() => ({}));
}

function refreshPreview() {
  // Cache-buster: the endpoint is no-store, but proxies in between may not be.
  el("preview").src = `api/preview.png?scale=4&t=${Date.now()}`;
}

function renderScenes(status) {
  const key = status.scenes.map((s) => `${s.name}:${s.ok}`).join("|");
  const select = el("scene");
  if (key !== sceneOptionsKey) {
    sceneOptionsKey = key;
    select.innerHTML = "";
    for (const scene of status.scenes) {
      if (!scene.ok) continue;
      const option = document.createElement("option");
      option.value = scene.name;
      option.textContent = scene.source === "user" ? `${scene.name} (user)` : scene.name;
      if (scene.description) option.title = scene.description;
      select.appendChild(option);
    }
  }
  if (select.value !== status.controls.active_scene) {
    select.value = status.controls.active_scene;
  }
}

function renderAlerts(status) {
  const alerts = [];
  for (const scene of status.scenes) {
    if (!scene.ok) {
      const detail = (scene.error || "").split("\n").pop();
      alerts.push(["error", `Scene "${scene.name}" failed to load: ${detail}`]);
    }
  }
  for (const [name, reason] of Object.entries(status.engine.quarantined || {})) {
    alerts.push(["error", `Scene "${name}" was quarantined after repeated errors: ${reason}`]);
  }
  if (status.engine.fallback_active) {
    alerts.push(["warn", `Falling back to "${status.engine.rendering_scene}".`]);
  }
  if (!status.home_assistant.configured) {
    alerts.push(["warn", "No Supervisor token: running without Home Assistant state."]);
  } else if (!status.home_assistant.available) {
    alerts.push(["warn", `Home Assistant state is stale: ${status.home_assistant.last_error || "unknown error"}`]);
  }
  el("alerts").innerHTML = alerts
    .map(([kind, text]) => `<div class="alert ${kind}">${text.replace(/[<>&]/g, "")}</div>`)
    .join("");
}

function renderDevices(status) {
  const body = el("devices").querySelector("tbody");
  if (!status.devices.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted">no devices connected</td></tr>';
    return;
  }
  body.innerHTML = status.devices
    .map(
      (device) => `<tr>
        <td>${device.device_id || "?"}<br><span class="muted">${device.fw_version || ""} ${device.resolution}</span></td>
        <td>${device.remote}</td>
        <td>${device.frames_sent}</td>
        <td>${device.rtt_ms === null ? "-" : device.rtt_ms + " ms"}</td>
        <td>${Math.round(device.connected_seconds)}s</td>
      </tr>`
    )
    .join("");
}

function renderStatus(status) {
  el("uptime").textContent = `up ${Math.round(status.uptime_seconds)}s · protocol v${status.protocol_version}`;
  el("stat-devices").textContent = `${status.device_count} connected (${status.server.total_connections} total)`;
  el("stat-scene").textContent =
    status.engine.rendering_scene && status.engine.rendering_scene !== status.controls.active_scene
      ? `${status.controls.active_scene} → ${status.engine.rendering_scene}`
      : status.controls.active_scene;
  el("stat-fps").textContent = `${status.engine.fps} / ${status.engine.target_fps} target`;
  el("stat-render").textContent = `${status.engine.last_render_ms} ms · ${status.engine.frames_rendered} frames`;
  const ha = status.home_assistant;
  el("stat-ha").textContent = !ha.configured
    ? "not connected"
    : `${ha.available ? "ok" : "stale"} · ${ha.lights_on}/${ha.lights_total} lights on`;
  el("stat-endpoint").textContent = `ws://<host>:${status.server.ws_port}${status.server.ws_path}`;
  el("scenes-dir").textContent = `Scenes: ${status.scenes_dir}`;

  if (!brightnessPending) {
    el("brightness").value = status.controls.brightness;
    el("brightness-value").textContent = status.controls.brightness;
  }
  const blank = el("blank");
  blank.setAttribute("aria-pressed", String(status.controls.blank));
  blank.textContent = status.controls.blank ? "Blanked" : "Blank";

  renderScenes(status);
  renderAlerts(status);
  renderDevices(status);
}

async function refreshStatus() {
  try {
    const response = await fetch("api/status", { cache: "no-store" });
    renderStatus(await response.json());
  } catch (error) {
    console.error("status fetch failed", error);
  }
}

el("scene").addEventListener("change", (event) => post("api/scene", { name: event.target.value }));

el("brightness").addEventListener("input", (event) => {
  brightnessPending = true;
  el("brightness-value").textContent = event.target.value;
});
el("brightness").addEventListener("change", async (event) => {
  await post("api/brightness", { value: Number(event.target.value) });
  brightnessPending = false;
});

el("blank").addEventListener("click", async () => {
  const pressed = el("blank").getAttribute("aria-pressed") === "true";
  await post("api/blank", { blank: !pressed });
  refreshStatus();
});

el("reload").addEventListener("click", async () => {
  await post("api/reload", {});
  sceneOptionsKey = "";
  refreshStatus();
});

refreshStatus();
refreshPreview();
setInterval(refreshStatus, STATUS_INTERVAL_MS);
setInterval(refreshPreview, Math.round(1000 / PREVIEW_FPS));
