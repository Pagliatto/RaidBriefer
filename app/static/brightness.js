// Monitor brightness UI; all monitor changes go through the existing server API.
(() => {
  const get = (id) => document.getElementById(id);
  const button = get("brightness-open");
  const popup = get("brightness-popup");
  const controls = get("brightness-controls");
  const slider = get("brightness-strength");
  const monitorSelect = get("brightness-monitor");
  const modeButtons = [...document.querySelectorAll("[data-brightness-mode]")];
  const presetButtons = [...document.querySelectorAll("[data-brightness-strength]")];
  let state = null;
  let transportError = "";
  let pendingStrength = null;
  let strengthTimer;
  let pending = 0;
  let revision = 0;
  let polling = false;
  let queue = Promise.resolve();
  let monitorSignature = "";

  function monitorName(monitor, detailed = false) {
    const name = (monitor.name || "").trim();
    const duplicate = name && state.monitors.some((m) => m.id !== monitor.id && (m.name || "").trim() === name);
    let label = name || t("brightness.monitorNumber", { number: monitor.number });
    if (duplicate) label = t("brightness.duplicateName", { name, number: monitor.number });
    if (monitor.primary) label += " · " + t("brightness.primary");
    if (detailed) {
      label = t("brightness.monitorDetails", { name: label, width: monitor.width, height: monitor.height });
    }
    return label;
  }

  function drawStrength(value) {
    slider.value = String(value);
    const label = t("brightness.percent", { value });
    get("brightness-value").textContent = label;
    slider.setAttribute("aria-valuetext", label);
    presetButtons.forEach((b) => {
      const active = Number(b.dataset.brightnessStrength) === value;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", String(active));
    });
  }

  function draw() {
    const message = transportError || state?.error || (!state ? t("brightness.loading") : !state.available ? t("brightness.unavailable") : "");
    const blocked = !state?.available || Boolean(message);
    button.dataset.state = !state?.available || transportError ? "unavailable"
      : state.overridden ? "overridden" : state.applied ? "applied" : state.mode === "off" ? "off" : "on";
    button.title = [t("brightness.title"), message || (state?.overridden ? t("brightness.overriddenTitle") : "")].filter(Boolean).join(" · ");
    get("brightness-message").textContent = message;
    get("brightness-message").hidden = !message;
    get("brightness-settings-message").textContent = message;
    get("brightness-settings-message").hidden = !message;
    controls.hidden = blocked;
    controls.disabled = blocked || pending > 0;
    popup.setAttribute("aria-busy", String(pending > 0));
    get("brightness-selected").hidden = blocked;
    // Keep monitor selection usable after a device-specific error, so it can be corrected.
    monitorSelect.disabled = !state?.available || Boolean(transportError) || pending > 0;
    get("brightness-identify").disabled = blocked || pending > 0;
    if (!state) return;
    modeButtons.forEach((b) => {
      const active = b.dataset.brightnessMode === state.mode;
      b.classList.toggle("active", active);
      b.setAttribute("aria-pressed", String(active));
    });
    if (pendingStrength !== null || !pending) drawStrength(pendingStrength ?? state.strength);
    const selected = state.monitor ? state.monitors.find((m) => m.id === state.monitor)
      : state.monitors.find((m) => m.primary) || state.monitors[0];
    get("brightness-selected").textContent = selected ? monitorName(selected) : t("brightness.monitorMissing");
    const note = get("brightness-note");
    note.textContent = t(state.overridden ? "brightness.overridden" : "brightness.borderless");
    note.classList.toggle("warn", Boolean(state.overridden));
    const signature = JSON.stringify([state.monitors, state.monitor]);
    if (signature !== monitorSignature) {
      const automatic = document.createElement("option");
      automatic.value = "";
      automatic.textContent = t("brightness.automatic");
      const options = state.monitors.map((monitor) => {
        const option = document.createElement("option");
        option.value = monitor.id;
        option.textContent = monitorName(monitor, true);
        return option;
      });
      if (state.monitor && !selected) {
        const missing = document.createElement("option");
        missing.value = state.monitor;
        missing.textContent = t("brightness.monitorMissing");
        missing.disabled = true;
        options.push(missing);
      }
      monitorSelect.replaceChildren(automatic, ...options);
      monitorSignature = signature;
    }
    monitorSelect.value = state.monitor;
    if (!popup.hidden) positionPopup();
  }

  async function request(path = "", body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(`/api/brightness${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error();
      return await response.json();
    } finally {
      clearTimeout(timeout);
    }
  }

  async function refresh() {
    if (polling || pending) return;
    polling = true;
    const started = revision;
    try {
      const next = await request();
      if (started !== revision) return;
      state = next;
      transportError = "";
    } catch (error) {
      if (started !== revision) return;
      transportError = t("brightness.failed");
    } finally {
      polling = false;
      draw();
    }
  }

  function save(path, body) {
    pending += 1;
    revision += 1;
    draw();
    queue = queue.then(async () => {
      try {
        state = await request(path, body);
        transportError = "";
        // Read back immediately after every change, including temporary effects.
        state = await request();
      } catch (error) {
        transportError = t("brightness.failed");
      } finally {
        pending -= 1;
        draw();
      }
    });
    return queue;
  }

  function flushStrength() {
    clearTimeout(strengthTimer);
    if (pendingStrength === null) return;
    const strength = pendingStrength;
    pendingStrength = null;
    save("", { strength });
  }

  function changeStrength(value) {
    pendingStrength = value;
    drawStrength(value);
    clearTimeout(strengthTimer);
    strengthTimer = setTimeout(flushStrength, 300);
  }

  function positionPopup() {
    const rect = button.getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    const top = Math.min(rect.bottom + 8, height - 48);
    popup.style.left = `${Math.max(8, Math.min(rect.right - popup.offsetWidth, width - popup.offsetWidth - 8))}px`;
    popup.style.top = `${top}px`;
    popup.style.maxHeight = `${Math.max(32, height - top - 8)}px`;
  }

  function closePopup(returnFocus = false) {
    if (popup.hidden || isWindowClosing(popup)) return;
    button.setAttribute("aria-expanded", "false");
    const restoreFocus = returnFocus || popup.contains(document.activeElement);
    animateClose(popup, () => { popup.hidden = true; }, "popover");
    if (restoreFocus) button.focus();
  }

  function openPopup() {
    cancelWindowMotion(popup);
    popup.hidden = false;
    button.setAttribute("aria-expanded", "true");
    positionPopup();
    animateOpen(popup, "popover");
    popup.focus();
    refresh();
  }
  // 버전 안내의 바로가기에서도 같은 열기 동작을 사용한다.
  window.openBrightnessPopup = openPopup;
  button.addEventListener("click", () => {
    if (!popup.hidden && !isWindowClosing(popup)) return closePopup(true);
    openPopup();
  });
  document.addEventListener("click", (event) => {
    if (!get("brightness-slot").contains(event.target)) closePopup();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popup.hidden) {
      event.preventDefault();
      closePopup(true);
    }
  });
  document.addEventListener("focusin", (event) => {
    if (!popup.hidden && !get("brightness-slot").contains(event.target)) closePopup();
  });
  window.addEventListener("resize", () => { if (!popup.hidden) positionPopup(); });
  get("settings-open").addEventListener("click", () => { closePopup(); refresh(); });
  modeButtons.forEach((b) => b.addEventListener("click", () => {
    flushStrength();
    save("", { mode: b.dataset.brightnessMode });
  }));
  slider.addEventListener("input", () => {
    clearTimeout(strengthTimer);
    pendingStrength = Number(slider.value);
    drawStrength(pendingStrength);
  });
  slider.addEventListener("change", () => changeStrength(Number(slider.value)));
  presetButtons.forEach((b) => b.addEventListener("click", () => changeStrength(Number(b.dataset.brightnessStrength))));
  get("brightness-preview").addEventListener("click", () => { flushStrength(); save("/preview", {}); });
  get("brightness-restore").addEventListener("click", () => {
    clearTimeout(strengthTimer);
    pendingStrength = null;
    save("/restore", {});
  });
  monitorSelect.addEventListener("change", () => {
    const monitor = monitorSelect.value;
    flushStrength();
    save("", { monitor });
  });
  get("brightness-identify").addEventListener("click", () => {
    const monitor = monitorSelect.value;
    flushStrength();
    save("/identify", { monitor });
  });
  [0, 3, 6, 9, 13, 18, 24, 32, 44, 60, 80].forEach((grey, index) => {
    const box = document.createElement("span");
    box.className = "brightness-step";
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = `rgb(${grey}, ${grey}, ${grey})`;
    swatch.setAttribute("aria-hidden", "true");
    box.append(swatch, String(index));
    get("brightness-steps").append(box);
  });
  draw();
  refresh();
  setInterval(refresh, 3000);
})();
