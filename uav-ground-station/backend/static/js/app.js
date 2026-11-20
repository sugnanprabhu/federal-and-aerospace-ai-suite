// SPDX-FileCopyrightText: (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0

(() => {
  const hostInput = document.getElementById("host-input");
  const refreshBtn = document.getElementById("refresh-btn");
  const cardsContainer = document.getElementById("cards-container");
  const tabsEl = document.getElementById("uav-tabs");
  const tabsListEl = document.getElementById("uav-tabs-list");
  const addUavBtn = document.getElementById("add-uav-tab-btn");
  const connectModal = document.getElementById("connect-modal");
  const connectModalClose = document.getElementById("connect-modal-close");
  const connectForm = document.getElementById("connect-form");
  const connectHostInput = document.getElementById("connect-host-input");
  const connectLabelInput = document.getElementById("connect-label-input");
  const connectErrorEl = document.getElementById("connect-form-error");

  // --- Multi-UAV tabs ---------------------------------------------------
  // One tab per host that's been connected to in this browser session.
  // Only one host's cards/metrics are shown at a time (switching tabs tears
  // down and rebuilds the visible dashboard for the newly active host, same
  // as a fresh connect), but a host's VSS live-sync indexing is NOT stopped
  // just because its tab isn't the active one -- see switchToTab()/closeTab()
  // below. The backend already tracks and reconciles every host it's been
  // told to sync independently, so this is purely a viewing convenience
  // on top of an already multi-host-capable backend.
  const sessions = new Map(); // host -> { connectedAt, label }

  // The most recent boot_id this tab has actually confirmed the backend is
  // running under. Sent with every POST /api/sessions so the server can tell
  // a *live* client apart from a tab that's been open since before a
  // restart and is still running with a stale value.
  let knownBootId = null;

  const SESSIONS_STORAGE_KEY = "uav-ground-station:sessions:v1";
  // The viz-backend process generates a random boot_id every time it starts.
  // We remember the last boot_id this browser saw; if the backend's current
  // boot_id doesn't match, the backend has restarted since we last talked to
  // it -- its in-memory `_sessions` registry is therefore genuinely empty,
  // and this browser's own localStorage-remembered hosts must NOT be used
  // to silently re-populate it.
  const BOOT_ID_STORAGE_KEY = "uav-ground-station:boot-id:v1";

  function persistSessions() {
    try {
      const hosts = Array.from(sessions.entries()).map(([host, s]) => ({ host, label: s.label || null }));
      localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify({ hosts, activeHost: currentHost }));
    } catch (_err) { /* localStorage unavailable -- degrade silently */ }
    if (currentHost && sessions.has(currentHost)) {
      const label = sessions.get(currentHost).label;
      fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: currentHost, label: label || null, boot_id: knownBootId }),
      }).then((res) => {
        if (res.status === 409) handleBackendRestartedMidSession();
      }).catch(() => {});
    }
  }

  async function fetchServerSessions() {
    try {
      const res = await fetch("/api/sessions");
      if (!res.ok) return null;
      const data = await res.json();
      if (!data || !Array.isArray(data.sessions)) return null;
      if (typeof data.boot_id === "string") knownBootId = data.boot_id;
      return {
        hosts: data.sessions.map((s) => ({ host: s.host, label: s.label || null })),
        activeHost: data.active_host || null,
        bootId: typeof data.boot_id === "string" ? data.boot_id : null,
      };
    } catch (_err) {
      return null;
    }
  }

  let handledBackendRestart = false;
  function handleBackendRestartedMidSession() {
    if (handledBackendRestart) return;
    handledBackendRestart = true;
    try { localStorage.removeItem(SESSIONS_STORAGE_KEY); } catch (_err) { /* ignore */ }
    for (const host of Array.from(hostViews.keys())) teardownHostView(host);
    sessions.clear();
    currentHost = null;
    hostInput.value = "";
    renderTabs();
    showNoHostsMessage();
    resetMetricsHeader();
    ChartManager.resetChart();
    openConnectModal();
    handledBackendRestart = false; // allow this to fire again after a future restart
  }

  // Returns true (and records the new boot_id) exactly once per backend
  // restart -- the first time any browser tab notices a new boot_id after
  // having seen a previous one. Returns false if this is the very first
  // time we've ever talked to this backend (nothing to compare against --
  // treat as "same", not "fresh", so a brand-new browser profile still
  // restores from local storage the way it always has) or if the boot_id
  // is unchanged.
  function backendRestartedSinceLastSeen(bootId) {
    if (!bootId) return false;
    let previous = null;
    try { previous = localStorage.getItem(BOOT_ID_STORAGE_KEY); } catch (_err) { /* ignore */ }
    try { localStorage.setItem(BOOT_ID_STORAGE_KEY, bootId); } catch (_err) { /* ignore */ }
    return previous !== null && previous !== bootId;
  }

  function readPersistedSessions() {
    try {
      const saved = JSON.parse(localStorage.getItem(SESSIONS_STORAGE_KEY) || "null");
      if (!saved || !Array.isArray(saved.hosts)) return null;
      const hosts = saved.hosts.filter((h) => h && typeof h.host === "string" && h.host);
      if (hosts.length === 0) return null;
      return { hosts, activeHost: saved.activeHost };
    } catch (_err) {
      return null;
    }
  }

  async function resolvePersistedSessions() {
    const [server, local] = await Promise.all([fetchServerSessions(), Promise.resolve(readPersistedSessions())]);
    if (!server) return local;
    if (backendRestartedSinceLastSeen(server.bootId)) {
      try { localStorage.removeItem(SESSIONS_STORAGE_KEY); } catch (_err) { /* ignore */ }
      return { hosts: server.hosts, activeHost: server.activeHost };
    }
    if (server.hosts.length === 0) return local;
    const localLabels = new Map((local?.hosts || []).map((h) => [h.host, h.label]));
    const hosts = server.hosts.map((h) => ({ host: h.host, label: h.label || localLabels.get(h.host) || null }));
    const activeHost = server.activeHost || local?.activeHost || hosts[0]?.host || null;
    return { hosts, activeHost };
  }

  function renderTabs() {
    tabsEl.classList.toggle("hidden", sessions.size === 0);
    tabsListEl.replaceChildren();
    for (const [host, session] of sessions) {
      const tab = document.createElement("div");
      tab.className = `uav-tab${host === currentHost ? " active" : ""}`;
      tab.setAttribute("role", "tab");
      const dot = document.createElement("span");
      // Every host -- not just the active tab -- keeps its own pipelines
      // poller/metrics/captions running in the background,
      // so its connState reflects real, continuously-refreshed health
      dot.className = `uav-tab-dot uav-tab-dot-${hostViews.get(host)?.connState || "off"}`;
      const label = document.createElement("button");
      label.type = "button";
      label.className = "uav-tab-label";
      label.textContent = session.label || host;
      label.title = session.label ? `${session.label} (${host})` : `Switch to ${host}`;
      label.addEventListener("click", () => { if (host !== currentHost) switchToTab(host); });
      const close = document.createElement("button");
      close.type = "button";
      close.className = "uav-tab-close";
      close.setAttribute("aria-label", `Disconnect ${host}`);
      close.title = `Disconnect ${host} (stops its background indexing too)`;
      close.textContent = "\u00d7";
      close.addEventListener("click", (e) => { e.stopPropagation(); closeTab(host); });
      tab.append(dot, label, close);
      tabsListEl.appendChild(tab);
    }
    document.dispatchEvent(new CustomEvent("uav:sessions-changed"));
    persistSessions();
  }

  window.UavSessions = {
    list: () => Array.from(sessions.entries()).map(([host, s]) => ({ host, label: s.label || null })),
    current: () => currentHost,
  };

  let localMetricsSource = null;
  let currentHost = null;
  let remoteHttpsPort = 443; // overwritten by /api/runtime-config
  let mediamtxWebrtcHost = "127.0.0.1"; // overwritten by /api/runtime-config
  let mediamtxWebrtcPort = 8889; // overwritten by /api/runtime-config

  // --- Per-host dashboard state ---------------------------------------------
  // One entry per connected UAV, kept alive for as long as its tab stays
  // open -- *not* just while it's the foreground/visible tab. So coming back to a
  // tab always looked like a fresh connect, with no history of what had
  // already been seen.
  const hostViews = new Map();

  function getOrCreateHostView(host) {
    let view = hostViews.get(host);
    if (!view) {
      cardsContainer.querySelectorAll(":scope > .cards-empty").forEach((n) => n.remove());
      const container = document.createElement("div");
      container.className = "host-view";
      container.dataset.host = host;
      container.style.display = "none";
      cardsContainer.appendChild(container);
      view = { container, cards: new Map(), captionsSource: null, captionsTopicsKey: "", chartBuffer: [], pollTimer: null, connState: "off" };
      hostViews.set(host, view);
    }
    return view;
  }

  function isHostConnected(host) {
    return hostViews.get(host)?.connState === "connected";
  }

  function setStatus(host, state) {
    const view = hostViews.get(host);
    if (!view) return;
    view.connState = state;
    renderTabs();
  }

  function renderEmptyState(host, message) {
    const view = getOrCreateHostView(host);
    view.container.innerHTML = "";
    if (!message) return;
    const empty = document.createElement("div");
    empty.className = "cards-empty";
    empty.textContent = message;
    view.container.appendChild(empty);
  }

  function clearEmptyState(host) {
    const view = hostViews.get(host);
    if (!view) return;
    const empty = view.container.querySelector(".cards-empty");
    if (empty) empty.remove();
  }

  // Show exactly one host's container at a time (its .device-card children
  // keep participating in cards-container's grid via "display: contents" on
  // the wrapper); every other host's view stays mounted in the DOM but hidden,
  // so its video/captions keep updating invisibly instead of being torn down.
  function showHostView(host) {
    for (const [h, view] of hostViews) {
      view.container.style.display = h === host ? "contents" : "none";
    }
  }

  // --- Caption formatting (mirrors live-video-captioning's run-card timeline) -
  const MAX_CAPTION_BUFFER = 20;
  const VISIBLE_CAPTION_LIMIT = MAX_CAPTION_BUFFER;

  function formatStreamSeconds(seconds) {
    if (!Number.isFinite(seconds)) return "—";
    const safeSeconds = Math.max(0, seconds);
    const minutes = Math.floor(safeSeconds / 60);
    const remaining = safeSeconds - minutes * 60;
    const secondsText = remaining.toFixed(2).padStart(5, "0");
    return `${String(minutes).padStart(2, "0")}:${secondsText}`;
  }

  function formatTimelinePositionLabel(index) {
    return index === 0 ? "Latest" : `latest -${index}`;
  }

  function formatCaptionTimestamp(data) {
    if (data && data.timestamp_seconds !== undefined) {
      return formatStreamSeconds(data.timestamp_seconds);
    }
    if (data && data.timestamp) {
      return `at ${new Date(data.timestamp).toLocaleTimeString()}`;
    }
    return `at ${new Date().toLocaleTimeString()}`;
  }

  function renderCaptionTimeline(card) {
    const entries = card.captionHistory.slice(0, VISIBLE_CAPTION_LIMIT);
    card.captionTimeline.innerHTML = "";
    if (entries.length === 0) {
      const empty = document.createElement("div");
      empty.className = "caption-entry caption-entry-placeholder";
      empty.textContent = "Waiting for captions…";
      card.captionTimeline.appendChild(empty);
      return;
    }
    entries.forEach((item, index) => {
      const row = document.createElement("article");
      row.className = "caption-entry";

      const meta = document.createElement("div");
      meta.className = "caption-entry-meta";
      meta.textContent = `${formatTimelinePositionLabel(index)} • ${item.timestampLabel}`;

      const text = document.createElement("p");
      text.className = "caption-entry-text";
      text.textContent = item.captionText;

      row.appendChild(meta);
      row.appendChild(text);
      card.captionTimeline.appendChild(row);
    });
  }

  function updateCaptionHistory(card, data, captionText) {
    card.captionHistory.unshift({
      captionText,
      timestampLabel: formatCaptionTimestamp(data),
    });
    if (card.captionHistory.length > MAX_CAPTION_BUFFER) {
      card.captionHistory.length = MAX_CAPTION_BUFFER;
    }
    renderCaptionTimeline(card);
  }

  // Update every card's lag chip every 100ms, same cadence as
  // live-video-captioning (it does the same thing across all its run cards)
  // -- across every connected host, not just the visible one, since
  // background hosts keep captioning live too.
  setInterval(() => {
    const now = Date.now();
    for (const view of hostViews.values()) {
      for (const card of view.cards.values()) {
        if (card.lastCaptionReceivedAt === null) continue;
        const lagSeconds = (now - card.lastCaptionReceivedAt) / 1000;
        card.chipLag.textContent = `${lagSeconds.toFixed(2)}s`;
      }
    }
  }, 100);

  // --- Pipeline auto-detection -----------------------------------------------
  // Finds every camera (a RUNNING pipeline with an RTSP stream) on the host,
  // so the user never has to pick one manually, and so every camera gets its
  // own card.
  async function detectPipelines(host) {
    try {
      const res = await fetch(`/api/detect-pipelines?host=${encodeURIComponent(host)}`);
      return await res.json();
    } catch (err) {
      return { reachable: false, cameras: [] };
    }
  }

  async function loadRuntimeConfig() {
    try {
      const res = await fetch("/api/runtime-config");
      const cfg = await res.json();
      // Leave the input empty; keep the placeholder generic.
      connectHostInput.value = "";
      connectHostInput.placeholder = "127.0.0.1";
      if (cfg.remoteHttpsPort) remoteHttpsPort = cfg.remoteHttpsPort;
      if (cfg.mediamtxWebrtcHost) mediamtxWebrtcHost = cfg.mediamtxWebrtcHost;
      if (cfg.mediamtxWebrtcPort) mediamtxWebrtcPort = cfg.mediamtxWebrtcPort;
      return cfg;
    } catch (err) {
      connectHostInput.value = "";
      connectHostInput.placeholder = "127.0.0.1";
      return {};
    }
  }

  // --- Card creation/teardown ------------------------------------------------
  function createCard(cam) {
    const wrap = document.createElement("section");
    wrap.className = "device-card panel";

    const header = document.createElement("div");
    header.className = "device-card-header";
    const title = document.createElement("div");
    title.className = "device-card-title";
    const cameraName = document.createElement("span");
    cameraName.className = "camera-name";
    cameraName.textContent = cam.title || cam.camera;
    const badges = document.createElement("span");
    badges.className = "device-badges";
    title.appendChild(cameraName);
    const infoBtn = document.createElement("button");
    infoBtn.type = "button";
    infoBtn.className = "btn btn-ghost btn-icon-only device-info-btn";
    infoBtn.title = "Loading pipeline details";
    infoBtn.setAttribute("aria-label", `${cam.title || cam.camera}: loading pipeline details`);
    infoBtn.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/></svg>';
    const headerRight = document.createElement("div");
    headerRight.className = "device-card-header-right";
    headerRight.appendChild(badges);
    headerRight.appendChild(infoBtn);
    header.appendChild(title);
    header.appendChild(headerRight);

    // Holds either an <iframe> (the local mediamtx's WHEP viewer for the
    // H.264 relay) or a placeholder <div> (no stream published) -- swapped
    // by syncVideoMode() below.
    const videoContainer = document.createElement("div");
    videoContainer.className = "device-video-container";
    const videoStage = document.createElement("div");
    videoStage.className = "device-video-stage";
    videoStage.appendChild(videoContainer);

    const captionPanel = document.createElement("div");
    captionPanel.className = "caption-panel";
    const chipsRow = document.createElement("div");
    chipsRow.className = "chips-row";
    const chips = document.createElement("div");
    chips.className = "chips";

    function makeChip(label) {
      const chip = document.createElement("span");
      chip.className = "chip";
      const strong = document.createElement("strong");
      strong.textContent = label;
      const value = document.createElement("span");
      value.textContent = "—";
      chip.appendChild(strong);
      chip.appendChild(value);
      chips.appendChild(chip);
      return value;
    }

    const chipTtft = makeChip("TTFT");
    const chipTpot = makeChip("TPOT");
    const chipThroughput = makeChip("Throughput");
    const chipLag = makeChip("Lag");

    const captionStatus = document.createElement("div");
    captionStatus.className = "caption-status";
    captionStatus.textContent = "—";

    chipsRow.appendChild(chips);
    chipsRow.appendChild(captionStatus);

    const captionTimeline = document.createElement("div");
    captionTimeline.className = "caption-timeline";

    captionPanel.appendChild(chipsRow);
    captionPanel.appendChild(captionTimeline);

    wrap.appendChild(header);
    wrap.appendChild(videoStage);
    wrap.appendChild(captionPanel);

    const card = {
      camera: cam.camera,
      topic: null,
      captioning: false,
      captionPanel,
      badgesEl: badges,
      cameraNameEl: cameraName,
      wrap,
      titleEl: title,
      infoEl: infoBtn,
      videoContainer,
      captionStatus,
      captionTimeline,
      chipTtft,
      chipTpot,
      chipThroughput,
      chipLag,
      captionHistory: [],
      lastCaptionReceivedAt: null,
      relayWatchdogTimer: null,
      lastRelayReady: null,
    };
    renderCaptionTimeline(card);
    renderBadges(card, cam);
    return card;
  }

  // Devices of the camera's pipeline as badges: detection device, VLM device (when captioning) and
  // whether detections are drawn on the stream.
  function renderBadges(card, info) {
    card.badgesEl.innerHTML = "";
    const add = (text, cls, tip) => {
      const badge = document.createElement("span");
      badge.className = `device-badge ${cls}`;
      badge.textContent = text;
      if (tip) badge.title = tip;
      card.badgesEl.appendChild(badge);
    };
    if (info.detection_device) {
      add(info.detection_device.toUpperCase(), `device-badge-${info.detection_device}`,
        `Object detection on ${info.detection_device.toUpperCase()}${info.detection_model ? ` (${info.detection_model})` : ""}`);
    }
    if (info.captioning) {
      add(info.vlm_device ? `VLM ${info.vlm_device.toUpperCase()}` : "VLM", "device-badge-vlm",
        `Captions by ${info.vlm_model || "the VLM"}${info.vlm_device ? ` on ${info.vlm_device.toUpperCase()}` : ""}`);
    }
    if (info.device) add("LIVE", "device-badge-live", `Live camera ${info.device}${info.camera_size ? ` (${info.camera_size})` : ""}`);
    if (info.overlay === false) add("no overlay", "device-badge-off", "Detection boxes and telemetry text are not drawn on this stream");
  }

  // Shows the caption panel and listens to the camera's MQTT topic only while the
  // pipeline captions. The actual MQTT/SSE subscription lives one level up, shared
  // across every captioning camera on this host -- this just updates this card's
  // own topic/UI state and asks that shared subscription to pick up the change.
  function applyCaptionState(host, card, topic) {
    const topicChanged = card.topic !== (topic || null);
    card.topic = topic || null;
    card.captioning = !!topic;
    card.captionPanel.hidden = !card.captioning;
    if (card.captioning && topicChanged) {
      card.captionHistory = [];
      card.lastCaptionReceivedAt = null;
      renderCaptionTimeline(card);
      card.chipTtft.textContent = "—";
      card.chipTpot.textContent = "—";
      card.chipThroughput.textContent = "—";
      card.chipLag.textContent = "—";
      card.captionStatus.textContent = "Listening for captions…";
    }
    refreshHostCaptionsStream(host);
  }

  function teardownCard(host, card) {
    stopRelayWatchdog(card);
    card.wrap.remove();
    refreshHostCaptionsStream(host); // drop this card's topic from the shared subscription, if any
  }

  // --- Video modes -----------------------------------------------------
  // "H.264 Relay" is the only video path: whenever a remote pipeline is
  // publishing RTSP for this camera (see "rtsp_available"; uav-vision-analytics
  // publishes H.264), the local viz-backend configures a mediamtx sidecar
  // (docker-compose-mediamtx.yml, always brought up alongside this service
  // -- see `make up`) to pull that RTSP stream on demand and re-serve it as
  // WebRTC -- pure remux, no transcoding -- then this iframes mediamtx's
  // own WHEP viewer page.
  async function startH264RelayStream(host, card) {
    card.videoContainer.innerHTML = "";
    const loading = document.createElement("div");
    loading.className = "device-video-placeholder";
    loading.textContent = "Starting H.264 relay…";
    card.videoContainer.appendChild(loading);
    try {
      const res = await fetch(
        `/api/relay-path?host=${encodeURIComponent(host)}&camera=${encodeURIComponent(card.camera)}`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error(`relay-path ${res.status}`);
      const { path } = await res.json();
      // Only swap the DOM in if this card is still in relay mode and
      // pointed at the same host -- avoids a slow response clobbering a
      // newer mode switch that happened while this fetch was in flight.
      // Also bail if the card's container was detached from the document
      // while this fetch was in flight otherwise we'd silently build a
      // working iframe into an orphaned node that's no longer rendered
      // anywhere, which looks exactly like "no video, no error".
      if (card.activeVideoMode !== "relay" || card.relayHost !== host || !card.videoContainer.isConnected) return;
      card.videoContainer.innerHTML = "";
      const iframe = document.createElement("iframe");
      iframe.className = "device-video device-video-webrtc";
      iframe.allow = "autoplay; fullscreen";
      iframe.allowFullscreen = true;
      // Behind the nginx overlay the page is HTTPS, so the WHEP viewer must come
      // from the same origin (nginx /mediamtx/) to avoid mixed content.
      iframe.src = location.protocol === "https:"
        ? `${location.origin}/mediamtx/${path}/`
        : `http://${mediamtxWebrtcHost}:${mediamtxWebrtcPort}/${path}/`;
      card.videoContainer.appendChild(iframe);
      setStatus(host, "connected");
      startRelayWatchdog(host, card);
    } catch (err) {
      card.videoContainer.innerHTML = "";
      const placeholder = document.createElement("div");
      placeholder.className = "device-video-placeholder";
      placeholder.textContent = "H.264 relay unavailable — is docker-compose-mediamtx.yml running?";
      card.videoContainer.appendChild(placeholder);
    }
  }

  // Polls /api/relay-status (a thin proxy onto mediamtx's own control API,
  // see main.py) so a WHEP session that has silently wedged -- the remote
  // RTSP publisher restarted out from under an already-open relay, or some
  // other transient mediamtx fault -- gets noticed and auto-recovered
  // without requiring the user to notice video is frozen and click the
  // refresh button themselves. This is the actual "automatic WHEP
  // renegotiation loop" half of the fix; bumping mediamtx's on-demand
  // timeouts (configs/mediamtx.yml) addresses the single most common cause
  // of the stall, but only this watchdog can recover from the stall if it
  // happens anyway.
  const RELAY_WATCHDOG_INTERVAL_MS = 15000;

  function startRelayWatchdog(host, card) {
    stopRelayWatchdog(card);
    card.lastRelayReady = null;
    card.relayWatchdogTimer = setInterval(async () => {
      // Bail if this card has moved on (different host/mode, or torn down)
      // since the timer was scheduled -- avoids a stray reload firing into
      // a card that's no longer showing this relay at all.
      if (card.activeVideoMode !== "relay" || card.relayHost !== host) {
        stopRelayWatchdog(card);
        return;
      }
      let ready = null;
      try {
        const res = await fetch(
          `/api/relay-status?host=${encodeURIComponent(host)}&camera=${encodeURIComponent(card.camera)}`,
        );
        if (res.ok) {
          const data = await res.json();
          ready = !!data.ready;
        }
      } catch (err) {
        return; // transient network/backend hiccup -- try again next tick, don't force-reload on a guess
      }
      if (card.activeVideoMode !== "relay" || card.relayHost !== host) return; // re-check after the await
      if (card.lastRelayReady === true && ready === false) {
        syncVideoMode(host, card, true, true); // force: re-negotiate a fresh WHEP session
      }
      card.lastRelayReady = ready;
    }, RELAY_WATCHDOG_INTERVAL_MS);
  }

  function stopRelayWatchdog(card) {
    if (card.relayWatchdogTimer) {
      clearInterval(card.relayWatchdogTimer);
      card.relayWatchdogTimer = null;
    }
    card.lastRelayReady = null;
  }

  function showNoStreamPlaceholder(card) {
    stopRelayWatchdog(card);
    card.videoContainer.innerHTML = "";
    const placeholder = document.createElement("div");
    placeholder.className = "device-video-placeholder";
    placeholder.textContent = "No video stream — start this camera's pipeline on the remote host.";
    card.videoContainer.appendChild(placeholder);
  }

  // --- Pipeline/model info (available from the header's information icon) ---
  async function refreshPipelineInfo(host, card, { force } = {}) {
    try {
      const res = await fetch(`/api/pipeline-info?host=${encodeURIComponent(host)}&camera=${encodeURIComponent(card.camera)}`);
      const info = await res.json();
      const state = info.reachable ? (info.state || "unknown") : "unreachable";
      const parts = [state];
      if (info.pipeline_name) parts.push(info.pipeline_name);
      const details = [];
      if (info.detection_device) details.push(`detection ${info.detection_device.toUpperCase()}${info.detection_model ? ` ${info.detection_model}` : ""}`);
      details.push(info.captioning
        ? `captions ${info.vlm_device ? info.vlm_device.toUpperCase() + " " : ""}${info.vlm_model || ""}`.trim()
        : "captioning off");
      if (info.prompt) details.push(`prompt: ${info.prompt}`);
      details.push(info.overlay === false ? "overlay off" : "overlay on");
      if (info.video) details.push(`video ${info.video}`);
      if (info.rtsp_url) details.push(`stream ${info.rtsp_url}`);
      if (info.device) details.push(`live camera ${info.device}${info.camera_size ? ` ${info.camera_size}` : ""}`);
      if (info.frame_rate !== null && info.frame_rate !== undefined) details.push(`${info.frame_rate} fps`);
      details.push(info.rtsp_available ? "H.264 Relay" : "no video stream");
      card.infoEl.title = [...parts, ...details].join(" • ");
      card.infoEl.setAttribute("aria-label", `${card.cameraNameEl.textContent}: ${card.infoEl.title}`);
      card.rtspPath = info.rtsp_path || card.camera;
      if (info.rtsp_available) {
        renderBadges(card, info);
        // The same stream path can be served by another pipeline after the configuration changed.
        const topic = info.captioning ? info.caption_topic : null;
        if (topic !== card.topic) applyCaptionState(host, card, topic);
      }
      syncVideoMode(host, card, info.rtsp_available, force);
    } catch (err) {
      card.infoEl.title = "Unable to load pipeline details";
      card.infoEl.setAttribute("aria-label", `${card.cameraNameEl.textContent}: ${card.infoEl.title}`);
    }
  }

  // Starts/swaps the card's video element to match whether the remote
  // pipeline is publishing, without restarting anything that's already
  // active and still correct (avoids tearing down a working WebRTC session
  // on every 5s pipeline-info poll). Shows the H.264 Relay (local mediamtx
  // remux) when a stream is published, otherwise a placeholder.
  //
  // `force`, when true, always reloads the iframe even if mode/host are
  // unchanged. This matters because mediamtx's own WHEP viewer page (what
  // the iframe points at) can get stuck showing its own terminal
  // "source ... has timed out" error -- e.g. if the remote RTSP publisher
  // was restarted (stop-rtsp/start-rtsp on the UAV) while mediamtx's
  // on-demand pull was active -- and does not necessarily recover on its
  // own; only loading the iframe fresh re-negotiates a new WHEP session.
  // Without `force`, syncVideoMode has no way to tell "still relay mode,
  // same host, but the existing iframe is wedged" apart from "still relay
  // mode, same host, still working fine" since it can't see into the
  // cross-origin iframe's content. Two things force a reload:
  //   - the user clicking the refresh button (an explicit "recheck now")
  //   - rtsp_available transitioning false -> true (the pipeline just
  //     (re)started -- e.g. the user ran `make start-rtsp` again -- so any
  //     existing iframe was necessarily showing a dead/placeholder state)
  function syncVideoMode(host, card, streamAvailable, force) {
    const mode = streamAvailable ? "relay" : "none";
    const cameFromUnavailable = !card.lastStreamAvailable && streamAvailable;
    card.lastStreamAvailable = streamAvailable;
    if (card.activeVideoMode === mode && card.relayHost === host && !force && !cameFromUnavailable) return;
    card.activeVideoMode = mode;
    card.relayHost = host;

    if (mode === "relay") {
      startH264RelayStream(host, card);
    } else {
      showNoStreamPlaceholder(card);
    }
  }

  // --- Metrics (CPU/GPU/NPU via metrics-manager SSE proxy, host-level) -----
  ChartManager.createConsolidatedChart('statsChart', [
    { key: 'cpu', label: 'CPU %', color: '#1ad0ff' },
    { key: 'mem', label: 'RAM %', color: '#8ca0c2' },
    { key: 'npu', label: 'NPU %', color: '#b388ff' },
  ]);

  // --- Metrics (this ground-station machine's own gs-metrics-manager) -----
  // Independent of the remote "connect" lifecycle -- always shows how much
  // local compute is spent on H.264 Relay remuxing / WHEP viewing here.
  // Same image/metric set as the remote chart, so it reuses applyMetricsTo.
  const GroundStationChartManager = createChartManager();
  GroundStationChartManager.createConsolidatedChart('gsStatsChart', [
    { key: 'cpu', label: 'CPU %', color: '#1ad0ff' },
    { key: 'mem', label: 'RAM %', color: '#8ca0c2' },
    { key: 'npu', label: 'NPU %', color: '#b388ff' },
  ]);

  function startLocalMetricsStream() {
    if (localMetricsSource) return;
    localMetricsSource = new EventSource('/api/local-metrics-stream');
    localMetricsSource.onmessage = (evt) => {
      let payload;
      try {
        payload = JSON.parse(evt.data);
      } catch (err) {
        return;
      }
      const frameSamples = buildFrameSamples(payload.metrics || []);
      if (frameSamples) renderMetricsFrame(frameSamples, GroundStationChartManager, 'gs');
    };
  }

  const CHART_MAX_POINTS = 60; // keep in sync with utils/charts.js's own maxPoints

  // One shared SSE connection covering EVERY connected host's metrics,
  // instead of one persistent /api/metrics-stream connection per host.
  let sharedMetricsSource = null;
  let sharedMetricsHostsKey = "";

  // Re-opens the shared metrics stream if the set of connected hosts
  // changed since it was last opened (EventSource URLs are immutable once
  // created, so a changed host list means closing and reconnecting) --
  // call this any time a hostView is created or torn down. No-op if the
  // host set is unchanged.
  function refreshSharedMetricsStream() {
    const hosts = Array.from(hostViews.keys());
    const key = hosts.slice().sort().join(",");
    if (key === sharedMetricsHostsKey && (hosts.length === 0 || sharedMetricsSource)) return;
    if (sharedMetricsSource) {
      sharedMetricsSource.close();
      sharedMetricsSource = null;
    }
    sharedMetricsHostsKey = key;
    if (hosts.length === 0) return;

    sharedMetricsSource = new EventSource(`/api/metrics-stream-multi?hosts=${encodeURIComponent(key)}`);
    sharedMetricsSource.onmessage = (evt) => {
      let payload;
      try {
        payload = JSON.parse(evt.data);
      } catch (err) {
        return;
      }
      const view = hostViews.get(payload.host);
      if (!view) return;
      const frameSamples = buildFrameSamples(payload.metrics || []);
      if (!frameSamples) return;
      view.chartBuffer.push(frameSamples);
      if (view.chartBuffer.length > CHART_MAX_POINTS) view.chartBuffer.shift();
      if (payload.host === currentHost) renderMetricsFrame(frameSamples, ChartManager, '');
    };
  }

  function setHeaderValue(id, value) {
    const el = document.getElementById(id);
    if (!el || value === undefined || value === null || Number.isNaN(value)) return;
    el.textContent = `${Math.max(0, Math.min(100, value)).toFixed(0)}%`;
  }

  function resetMetricsHeader() {
    const cpuVal = document.getElementById("cpuVal");
    if (cpuVal) cpuVal.textContent = "—";
    const ramVal = document.getElementById("ramVal");
    if (ramVal) ramVal.textContent = "—";
    const npuVal = document.getElementById("npuVal");
    if (npuVal) npuVal.textContent = "—";
    const npuStat = document.getElementById("npuStat");
    if (npuStat) npuStat.style.display = "none";
    const gpuValues = document.getElementById("gpuValues");
    if (gpuValues) {
      gpuValues.innerHTML = "";
      gpuValues.style.display = "none";
    }
  }

  // Pure parser for a metrics-manager SSE payload -> a flat frameSamples
  // object ({cpu, mem, npu, "gpu:<id>": ...}), with no DOM/chart side
  // effects -- safe to call for a host that isn't currently on screen, so
  // its samples can still be buffered for an instant replay once its tab
  // becomes visible again.
  function buildFrameSamples(metrics) {
    const frameSamples = {};
    const gpuUsageByDevice = new Map();
    for (const m of metrics) {
      if (m.name === "cpu_usage_user") frameSamples.cpu = m.value;
      if (m.name === "mem_used_percent") frameSamples.mem = m.value;
      if (m.name === "npu_utilization") frameSamples.npu = m.value;
      if (m.name === "gpu_engine_usage_usage") {
        const gpuId = String((m.labels && m.labels.gpu_id) || "0");
        gpuUsageByDevice.set(gpuId, Math.max(gpuUsageByDevice.get(gpuId) || 0, m.value));
      }
    }
    for (const [gpuId, usage] of gpuUsageByDevice) frameSamples[`gpu:${gpuId}`] = usage;
    return Object.keys(frameSamples).length > 0 ? frameSamples : null;
  }

  // Applies one already-parsed frameSamples object to the on-screen header
  // numbers/chart -- only ever called for whichever host is currently
  // visible (`idPrefix` picks which panel's DOM ids to update: "" for the
  // remote "UAV Companion Board" panel's cpuVal/ramVal/npuVal/npuStat/
  // gpuValues, "gs" for this ground station's own gsCpuVal/gsRamVal/
  // gsNpuVal/gsNpuStat/gsGpuValues).
  function renderMetricsFrame(frameSamples, chartManager, idPrefix) {
    // "" (remote panel) uses lowerCamelCase ids (cpuVal, npuStat, ...);
    // "gs" (ground station panel) prefixes the capitalized suffix (gsCpuVal, gsNpuStat, ...).
    const id = (suffix) => (idPrefix ? `${idPrefix}${suffix}` : suffix.charAt(0).toLowerCase() + suffix.slice(1));

    if (frameSamples.cpu !== undefined) setHeaderValue(id("CpuVal"), frameSamples.cpu);
    if (frameSamples.mem !== undefined) setHeaderValue(id("RamVal"), frameSamples.mem);
    if (frameSamples.npu !== undefined) {
      setHeaderValue(id("NpuVal"), frameSamples.npu);
      const npuStat = document.getElementById(id("NpuStat"));
      if (npuStat) npuStat.style.display = "";
    }

    const gpuKeys = Object.keys(frameSamples).filter((k) => k.startsWith("gpu:"));
    if (gpuKeys.length > 0) {
      const gpuValues = document.getElementById(id("GpuValues"));
      if (gpuValues) gpuValues.innerHTML = "";
      gpuKeys
        .sort((a, b) => (Number(a.slice(4)) || 0) - (Number(b.slice(4)) || 0))
        .forEach((key) => {
          const gpuId = key.slice(4);
          const usage = frameSamples[key];
          if (gpuValues) {
            const chip = document.createElement("span");
            chip.className = "gpu-value-chip";
            chip.style.color = chartManager.getSeriesColor(key);
            chip.textContent = `GPU-${gpuId} ${usage.toFixed(0)}%`;
            gpuValues.appendChild(chip);
          }
        });
      if (gpuValues) gpuValues.style.display = "";
    }

    chartManager.pushStatFrame(frameSamples);
  }


  // --- Captions (MQTT bridged via SSE) --------------------------------------
  // One shared SSE/MQTT connection per HOST, covering every captioning
  // camera on that host, instead of one connection per camera.
  // Call whenever a card's topic changes or a card is added/removed;
  // no-op if this host's set of captioning topics hasn't actually changed.
  //
  // Event payload shape (tagged with its topic by the backend's
  // /api/captions-stream-multi, wrapping the gvagenai -> MQTTPublisher
  // gvapython stage's own payload):
  //   { "topic": "uav/caption/<name>",
  //     "payload": { "metadata": { "result": "<caption text>",
  //                                 "timestamp_seconds": <float>,
  //                                 "timestamp": <int ns>, "metrics": {...}, ... },
  //                  "blob": "" } }
  function refreshHostCaptionsStream(host) {
    const view = hostViews.get(host);
    if (!view) return;
    const topics = Array.from(view.cards.values()).filter((c) => c.topic).map((c) => c.topic);
    const key = topics.slice().sort().join(",");
    if (key === view.captionsTopicsKey && (topics.length === 0 || view.captionsSource)) return;
    if (view.captionsSource) {
      view.captionsSource.close();
      view.captionsSource = null;
    }
    view.captionsTopicsKey = key;
    if (topics.length === 0) return;

    view.captionsSource = new EventSource(
      `/api/captions-stream-multi?host=${encodeURIComponent(host)}&topics=${encodeURIComponent(key)}`,
    );
    view.captionsSource.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch (err) {
        return;
      }
      const card = Array.from(view.cards.values()).find((c) => c.topic === msg.topic);
      if (!card) return; // card for this topic was removed/changed since the event was queued

      const payload = msg.payload;
      const data = (payload && typeof payload === "object" && payload.metadata) ? payload.metadata : payload;
      const captionText = data && typeof data === "object" && data.result
        ? data.result
        : (typeof data === "string" ? data : JSON.stringify(data));
      updateCaptionHistory(card, data, captionText);

      const metrics = (data && typeof data === "object" && data.metrics) ? data.metrics : {};
      card.chipTtft.textContent = Number.isFinite(metrics.ttft_mean) ? `${metrics.ttft_mean.toFixed(0)} ms` : "—";
      card.chipTpot.textContent = Number.isFinite(metrics.tpot_mean) ? `${metrics.tpot_mean.toFixed(2)} ms` : "—";
      card.chipThroughput.textContent = Number.isFinite(metrics.throughput_mean) ? `${metrics.throughput_mean.toFixed(2)} tok/s` : "—";
      card.lastCaptionReceivedAt = Date.now();
      card.chipLag.textContent = "0.00s";

      card.captionStatus.textContent = data && data.timestamp_seconds !== undefined
        ? `Updated ${data.timestamp_seconds.toFixed(2)}s into stream`
        : data && data.timestamp
          ? `Updated at ${new Date(data.timestamp).toLocaleTimeString()}`
          : "—";
    };
    view.captionsSource.addEventListener("connected", () => {
      for (const card of view.cards.values()) {
        if (card.captioning) card.captionStatus.textContent = "Listening for captions…";
      }
    });
  }

  // Fully tears down one host's background view: closes its metrics SSE,
  // stops its 5s poll loop, closes every card's caption SSE and removes its
  // DOM, then drops the host's wrapper container and hostViews entry
  // entirely. This is the ONLY place a host's dashboard state is actually
  // discarded -- used solely for an explicit disconnect/close-tab, never for
  // a plain tab switch.
  function teardownHostView(host) {
    const view = hostViews.get(host);
    if (!view) return;
    if (view.captionsSource) view.captionsSource.close();
    if (view.pollTimer) clearInterval(view.pollTimer);
    // Just drop the DOM here (not teardownCard(), which would also try to
    // refresh/reopen this host's shared captions subscription per-card --
    // pointless since the whole hostView, including that subscription,
    // is being discarded as a unit right below).
    for (const card of view.cards.values()) card.wrap.remove();
    view.container.remove();
    hostViews.delete(host);
    refreshSharedMetricsStream(); // drop this host from the shared metrics connection
  }

  function connectCard(host, card, cam) {
    // Video is started by refreshPipelineInfo()'s syncVideoMode() once we
    // know whether a pipeline is publishing for this camera;
    // card.activeVideoMode starts unset so that first call always (re)starts
    // it.
    card.activeVideoMode = null;
    applyCaptionState(host, card, cam.captioning ? cam.mqtt_topic : null);
    refreshPipelineInfo(host, card);
  }

  // Reconciles `host`'s card grid against its currently-detected running
  // cameras -- entirely within that host's own hostView, independent of
  // whether `host` is the tab currently on screen:
  //  - a camera that's newly running -> add a card, start its streams.
  //  - a camera that's no longer running -> remove its card, stop its streams.
  //  - a camera still running -> just refresh its pipeline-info text (its
  //    caption SSE/history is left completely untouched, so captions keep
  //    accumulating across tab switches instead of resetting to zero).
  function syncCards(host, detectedCameras, { force } = {}) {
    const view = getOrCreateHostView(host);
    const detectedSet = new Set(detectedCameras.map((c) => c.camera));

    for (const [camera, card] of Array.from(view.cards.entries())) {
      if (!detectedSet.has(camera)) {
        view.cards.delete(camera); // delete first: teardownCard() below recomputes this host's shared captions subscription from what's left in view.cards
        teardownCard(host, card);
      } else {
        refreshPipelineInfo(host, card, { force });
      }
    }

    // Insert new cards at their position in the backend's camera order, so a
    // camera whose pipeline starts late doesn't end up last.
    detectedCameras.forEach((cam, idx) => {
      if (!view.cards.has(cam.camera)) {
        clearEmptyState(host);
        const card = createCard(cam);
        const next = detectedCameras.slice(idx + 1).map((c) => view.cards.get(c.camera)).find(Boolean);
        view.container.insertBefore(card.wrap, next ? next.wrap : null);
        view.cards.set(cam.camera, card);
        connectCard(host, card, cam);
      }
    });

    if (view.cards.size === 0) {
      renderEmptyState(host, "No running pipeline detected on this host.");
      setStatus(host, "off");
    } else {
      setStatus(host, "connected");
    }
  }

  // Ensures `host` has a live hostView with its metrics stream and 5s
  // poll-and-reconcile loop running -- idempotent, so it's safe to call
  // every time a host becomes "the one we care about keeping live", whether
  // that's a brand-new connect, switching to an already-connected tab, or
  // restoring a background tab on page load.
  function ensureHostActive(host) {
    const view = getOrCreateHostView(host);
    refreshSharedMetricsStream();
    if (!view.pollTimer) {
      view.pollTimer = setInterval(() => pollAndReconcile(host), 5000);
    }
    return view;
  }

  async function pollAndReconcile(host, { force } = {}) {
    const detected = await detectPipelines(host);
    if (!hostViews.has(host)) return; // host was disconnected while this request was in flight
    if (!detected.reachable) {
      // Host went unreachable -- leave existing cards as-is (video/caption
      // streams will show their own connection errors) rather than tearing
      // everything down on a possibly-transient network blip.
      return;
    }
    syncCards(host, detected.cameras || [], { force });
    const existing = sessions.get(host);
    if (existing) existing.lastCameras = detected.cameras || [];
  }

  // Connects to `host` for the first time: creates its background hostView
  // (metrics + 5s reconcile loop, both of which keep running for as long as
  // the tab stays open, regardless of whether it's visible), then makes it
  // the visible tab.
  async function connectNow(label) {
    const host = (hostInput.value || "127.0.0.1").trim();
    currentHost = host;
    const view = ensureHostActive(host);
    showHostView(host);
    replayChartBuffer(host);
    resetMetricsHeader();
    if (view.cards.size === 0) {
      setStatus(host, "connecting");
      renderEmptyState(host, "Detecting running pipelines…");
    }

    const detected = await detectPipelines(host);
    if (!hostViews.has(host)) return; // disconnected while this request was in flight
    if (!detected.reachable) {
      if (view.cards.size === 0) {
        renderEmptyState(host, "Host unreachable.");
        setStatus(host, "off");
      }
      // else: keep showing the existing cards; the poll loop will retry.
    } else {
      syncCards(host, detected.cameras || []);
    }
    // Re-connecting to an already-known host (e.g. the connect modal was
    // used again for a host that's already open in another tab) keeps its
    // existing label/cache rather than clearing them.
    const existing = sessions.get(host);
    sessions.set(host, {
      connectedAt: existing?.connectedAt ?? Date.now(),
      label: label ?? existing?.label,
      lastCameras: detected.reachable ? (detected.cameras || []) : (existing?.lastCameras || []),
    });
    renderTabs();
    document.dispatchEvent(new CustomEvent("uav:connected", { detail: { host } }));
  }

  // Switch the visible dashboard to an already-connected host's tab. This is
  // a pure visibility toggle -- it does NOT stop or restart any stream: the
  // newly-visible host's video/captions/metrics were already running in the
  // background, so nothing resets and no history is lost.
  function switchToTab(host) {
    currentHost = host;
    showHostView(host);
    hostInput.value = host;
    renderTabs();
    replayChartBuffer(host);
    resetMetricsHeader();
    document.dispatchEvent(new CustomEvent("uav:connected", { detail: { host } }));
    // Opportunistic refresh so the tab looks up-to-date the moment it's
    // shown, instead of waiting for the next 5s poll tick -- non-destructive,
    // since syncCards() only patches the diff against existing cards.
    pollAndReconcile(host);
  }

  // The CPU/GPU/NPU chart (ChartManager) is one shared Chart.js instance,
  // not per-host -- recreating N Chart.js instances for N UAVs was judged
  // not worth the memory/perf cost for what's just a header sparkline, so
  // instead each host buffers its own recent samples (chartBuffer, see
  // refreshSharedMetricsStream()) and this replays them into the shared chart the
  // instant its tab becomes visible, so switching tabs shows that host's own
  // recent trend immediately rather than an empty chart or (worse) a
  // leftover trace from whichever host was previously on screen.
  function replayChartBuffer(host) {
    ChartManager.resetChart();
    const view = hostViews.get(host);
    if (!view) return;
    for (const frameSamples of view.chartBuffer) ChartManager.pushStatFrame(frameSamples);
  }

  function showNoHostsMessage() {
    cardsContainer.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "cards-empty";
    empty.textContent = "No UAVs connected. Click \u201cAdd UAV\u201d to connect one.";
    cardsContainer.appendChild(empty);
  }

  // Fully disconnects one host: tears down its background view (stopping
  // its metrics/poll loop/caption SSE and discarding its caption history --
  // this is the one case that's supposed to happen), removes it from the
  // tab bar and the server-side session registry, and -- if it was the
  // active tab -- switches to another remaining tab (or shows the "no UAVs
  // connected" state / re-opens the connect modal if that was the last one).
  function closeTab(host) {
    const wasActive = host === currentHost;
    teardownHostView(host);
    sessions.delete(host);
    fetch("/api/sessions", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host }),
    }).catch(() => {});
    document.dispatchEvent(new CustomEvent("uav:disconnected", { detail: { host } }));

    if (wasActive) {
      currentHost = null;
      hostInput.value = "";
      const remaining = sessions.keys().next().value;
      if (remaining) {
        switchToTab(remaining);
        return;
      }
    }
    renderTabs();
    if (sessions.size === 0) {
      showNoHostsMessage();
      resetMetricsHeader();
      ChartManager.resetChart();
      openConnectModal();
    }
  }

  function disconnectNow() {
    if (currentHost) closeTab(currentHost);
  }

  // --- Connect modal ("Add UAV") -----------------------------------------
  let connectModalLastFocus = null;

  function openConnectModal() {
    connectModalLastFocus = document.activeElement;
    connectErrorEl.textContent = "";
    connectLabelInput.value = "";
    connectModal.classList.remove("hidden");
    connectHostInput.focus();
    connectHostInput.select();
  }

  function closeConnectModal() {
    // Closing without ever having connected a UAV would leave a dead-end,
    // empty dashboard with no way back in -- keep the modal open in that case.
    if (sessions.size === 0) return;
    connectModal.classList.add("hidden");
    if (connectModalLastFocus) connectModalLastFocus.focus();
  }

  connectModalClose.addEventListener("click", closeConnectModal);
  connectModal.addEventListener("click", (e) => { if (e.target === connectModal) closeConnectModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !connectModal.classList.contains("hidden")) closeConnectModal();
  });
  addUavBtn.addEventListener("click", openConnectModal);

  connectForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const host = connectHostInput.value.trim();
    const label = connectLabelInput.value.trim();
    if (!host) {
      connectErrorEl.textContent = "Enter a host IP or hostname.";
      return;
    }
    connectErrorEl.textContent = "";
    connectModal.classList.add("hidden");
    if (sessions.has(host)) {
      // Already connected -- just bring its tab forward instead of
      // reconnecting (which would needlessly restart its video/metrics
      // streams).
      if (host !== currentHost) switchToTab(host);
      return;
    }
    hostInput.value = host;
    await connectNow(label || undefined);
  });

  refreshBtn.addEventListener("click", () => {
    if (!currentHost || !sessions.has(currentHost)) return;
    // force: true -- also reload any video iframe even if its mode/host
    // look unchanged, so a WHEP viewer wedged on mediamtx's own "source ...
    // has timed out" error actually gets refreshed by this button instead
    // of silently no-op'ing until a full page reload.
    pollAndReconcile(currentHost, { force: true });
  });

  // Auto-reconnect to every host that was connected before the last reload
  // (or in another tab of this browser), restoring tabs instantly from the
  // persisted host list, then bringing each one's dashboard/VSS live-sync
  // back up the same way a fresh connect would. Returns true if anything was
  // restored (so the caller can skip opening the connect modal).
  async function restorePersistedSessions() {
    const saved = await resolvePersistedSessions();
    if (!saved || !saved.hosts || saved.hosts.length === 0) return false;

    // Pre-populate sessions so tabs render immediately, before any network
    // round trip -- avoids a blank dashboard while each host is re-probed.
    for (const h of saved.hosts) {
      sessions.set(h.host, { connectedAt: Date.now(), label: h.label || undefined, lastCameras: [] });
    }
    renderTabs();

    const activeHost = sessions.has(saved.activeHost) ? saved.activeHost : saved.hosts[0].host;

    // Bring the active tab's dashboard up exactly like a normal connect.
    hostInput.value = activeHost;
    await connectNow(sessions.get(activeHost)?.label);

    // Other restored hosts stay as background tabs: no visible dashboard
    // switch, but still re-reconcile their camera state and re-arm VSS
    // live-sync indexing (mirrors what a real connect does for them). Also
    // (re-)register each with the server-side registry (active=false, so
    // this doesn't steal "active tab" from activeHost) -- covers the
    // localStorage-only fallback path, where the server may not have known
    // about these hosts yet.
    for (const h of saved.hosts) {
      if (h.host === activeHost) continue;
      ensureHostActive(h.host);
      renderEmptyState(h.host, "Detecting running pipelines…");
      pollAndReconcile(h.host);
      document.dispatchEvent(new CustomEvent("uav:connected", { detail: { host: h.host, background: true } }));
      fetch(`/api/sessions?active=false`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: h.host, label: h.label || null, boot_id: knownBootId }),
      }).then((res) => {
        if (res.status === 409) handleBackendRestartedMidSession();
      }).catch(() => {});
    }
    return true;
  }

  loadRuntimeConfig().then(async () => {
    let restored = false;
    try {
      restored = await restorePersistedSessions();
    } catch (err) {
      console.error("restorePersistedSessions failed, falling back to connect modal", err);
    }
    // No UAVs connected/restored -- the connect modal is the only way to get
    // a dashboard going, so open it immediately.
    if (!restored && sessions.size === 0) openConnectModal();
  });
  startLocalMetricsStream();
})();

