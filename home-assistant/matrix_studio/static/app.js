// Matrix Studio ingress UI. Plain DOM, no build step, no dependencies.
// All URLs are relative so the page works under Home Assistant's ingress
// prefix (set via <base href> by web.py) and standalone.

const STATUS_INTERVAL_MS = 1000;

const el = (id) => document.getElementById(id);
let sceneOptionsKey = "";
let otaDevicesKey = "";
let brightnessPending = false;
let otaRequestInFlight = false;
let previewFrame = "";
let previewObjectUrl = "";

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

async function refreshPreview() {
  try {
    // The request waits for the next rendered frame. This keeps preview
    // presentation on the engine clock instead of beating against a separate
    // browser timer, while still guaranteeing that requests never overlap.
    const response = await fetch(`api/preview.png?scale=1&after=${previewFrame}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`preview request failed: HTTP ${response.status}`);
    const nextFrame = response.headers.get("X-Matrix-Frame") || "";
    const blob = await response.blob();
    const nextUrl = URL.createObjectURL(blob);
    const image = el("preview");
    image.src = nextUrl;
    await image.decode().catch(() => {});
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = nextUrl;
    if (nextFrame === "blank") {
      await new Promise((resolve) => setTimeout(resolve, 1000));
    } else if (nextFrame === previewFrame) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    previewFrame = nextFrame;
    requestAnimationFrame(refreshPreview);
  } catch (error) {
    console.error("preview refresh failed", error);
    setTimeout(refreshPreview, 500);
  }
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
    body.innerHTML = '<tr><td colspan="6" class="muted">no devices connected</td></tr>';
    return;
  }
  body.innerHTML = status.devices
    .map(
      (device) => `<tr>
        <td>${device.device_id || "?"}<br><span class="muted">${device.fw_version || ""} ${device.resolution}</span></td>
        <td>${device.remote}</td>
        <td>${device.frames_sent}<br><span class="muted">${device.frames_dropped} skipped</span></td>
        <td>${status.controls.blank ? "paused" : device.cadence_stale ? "stalled" : device.send_fps === null ? "-" : device.send_fps + " fps"}<br><span class="muted">${
          status.controls.blank
            ? "stream paused"
            : device.cadence_stale
            ? `${device.last_frame_age} s since frame · ${device.max_frame_gap_ms} ms gap`
            : device.send_jitter_ms === null
              ? "warming up"
              : `±${device.send_jitter_ms} ms · ${device.max_frame_gap_ms} ms max`
        }</span></td>
        <td>${device.rtt_ms === null ? "-" : device.rtt_ms + " ms"}</td>
        <td>${Math.round(device.connected_seconds)}s</td>
      </tr>`
    )
    .join("");
}

function renderFirmware(status) {
  const select = el("ota-device");
  const previous = select.value;
  const key = status.devices.map((device) => `${device.id}:${device.device_id}:${device.fw_version}`).join("|");
  if (key !== otaDevicesKey) {
    otaDevicesKey = key;
    select.innerHTML = "";
    for (const device of status.devices) {
      const option = document.createElement("option");
      option.value = String(device.id);
      option.textContent = `${device.device_id || device.remote} · ${device.fw_version || "unknown firmware"}`;
      select.appendChild(option);
    }
    if (status.devices.some((device) => String(device.id) === previous)) {
      select.value = previous;
    }
  }

  select.disabled = status.devices.length === 0 || otaRequestInFlight;
  const selected = status.devices.find((device) => String(device.id) === select.value);
  const button = el("ota-install");
  button.disabled = status.devices.length === 0 || otaRequestInFlight || Boolean(selected?.ota?.active);

  if (!selected) {
    if (!otaRequestInFlight) el("ota-status").textContent = "No device connected.";
    return;
  }

  if (selected.ota?.active) {
    const sent = Number(selected.ota.bytes_sent || 0);
    const total = Number(selected.ota.total_bytes || 0);
    const percent = total ? Math.floor((sent * 100) / total) : 0;
    el("ota-status").textContent = `Installing firmware… ${percent}% (${sent}/${total} bytes)`;
  } else if (selected.ota?.last_error && !otaRequestInFlight) {
    el("ota-status").textContent = `Last update failed: ${selected.ota.last_error}`;
  }
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
  renderFirmware(status);
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

el("ota-install").addEventListener("click", async () => {
  const connectionId = el("ota-device").value;
  const file = el("ota-file").files[0];
  if (!connectionId) {
    el("ota-status").textContent = "Select a connected device.";
    return;
  }
  if (!file) {
    el("ota-status").textContent = "Choose matrix_studio.bin first.";
    return;
  }

  otaRequestInFlight = true;
  el("ota-install").disabled = true;
  el("ota-device").disabled = true;
  el("ota-status").textContent = `Starting update with ${file.name} (${file.size} bytes)…`;

  try {
    const response = await fetch(`api/ota?connection_id=${encodeURIComponent(connectionId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(result.error || `request failed with HTTP ${response.status}`);
    }
    el("ota-status").textContent = result.message || "Firmware committed; device rebooting.";
  } catch (error) {
    el("ota-status").textContent = `Update failed: ${error.message || error}`;
  } finally {
    otaRequestInFlight = false;
    refreshStatus();
  }
});

refreshStatus();
refreshPreview();
setInterval(refreshStatus, STATUS_INTERVAL_MS);
