// SPDX-FileCopyrightText: (C) 2026 Intel Corporation
// SPDX-License-Identifier: Apache-2.0
//
// Runs the real dashboard (backend/static/index.html + js/app.js) in a simulated browser (jsdom) against a
// scripted backend, and checks the per-camera cards: badges, caption panel, caption subscriptions and relay.
//
//   cd uav-ground-station/tests && npm install jsdom && node dashboard_cards.test.js
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const STATIC = path.join(__dirname, "..", "backend", "static");
let failures = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? `  (${detail})` : ""}`);
  if (!ok) failures += 1;
}
const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

// What /api/detect-pipelines and /api/pipeline-info return for a camera (same shape as backend/main.py).
function camera(name, over = {}) {
  return Object.assign({
    camera: `uav-${name}-camera`, title: `${name} camera`, pipeline_name: "uav_object_detection_npu_gpu", state: "RUNNING",
    detection_device: "npu", detection_model: "yolo11s.xml", captioning: true, vlm_device: "gpu", vlm_model: "InternVL2-1B",
    prompt: "Describe what you see in the scene.", max_new_tokens: 50, mqtt_topic: `uav/caption/${name}_camera`,
    overlay: true, video: `uav_sample_${name}.avi`, label: `${name}-camera-NPU-GPU-Caption`, frame_rate: null,
  }, over);
}

async function main() {
  const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8").replace(/<script[\s\S]*?<\/script>/g, "");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost:8080/" });
  const { window } = dom;
  const { document } = window;

  // --- scripted backend ------------------------------------------------------------------------------
  let cameras = [camera("front"), camera("bottom"), camera("back")];
  let reachable = true;
  const requests = [];
  window.fetch = async (url) => {
    requests.push(url);
    const respond = (body) => ({ ok: true, status: 200, json: async () => body });
    if (url.startsWith("/api/runtime-config")) return respond({ defaultHost: "10.223.23.75", remoteHttpsPort: 443, mediamtxWebrtcHost: "127.0.0.1", mediamtxWebrtcPort: 8889 });
    if (url.startsWith("/api/detect-pipelines")) return respond({ reachable, cameras: reachable ? cameras : [] });
    if (url.startsWith("/api/pipeline-info")) {
      const cam = decodeURIComponent(new URL(url, "http://x").searchParams.get("camera"));
      const record = cameras.find((c) => c.camera === cam);
      return respond(record ? Object.assign({ reachable: true, rtsp_available: true, rtsp_path: cam, caption_topic: record.mqtt_topic }, record)
                            : { reachable: true, camera: cam, rtsp_available: false, captioning: false });
    }
    if (url.startsWith("/api/relay-path")) {
      const cam = decodeURIComponent(new URL(url, "http://x").searchParams.get("camera"));
      return respond({ path: `relay-${cam}` });
    }
    return respond({});
  };
  const sources = [];
  window.EventSource = class {
    constructor(url) { this.url = url; this.closed = false; sources.push(this); }
    addEventListener() {}
    close() { this.closed = true; }
  };
  const chartManager = () => ({ createConsolidatedChart() {}, resetChart() {}, updateChart() {}, update() {} });
  window.createChartManager = chartManager;
  window.eval("var ChartManager = createChartManager();");        // charts.js declares it as a global
  let pollTimer = null;
  const realSetInterval = window.setInterval.bind(window);
  window.setInterval = (fn, ms) => { if (ms === 5000) { pollTimer = fn; return 1; } return realSetInterval(fn, ms); };

  window.eval(fs.readFileSync(path.join(STATIC, "js", "app.js"), "utf8"));
  await tick();
  const cards = () => Array.from(document.querySelectorAll(".device-card"));
  const card = (name) => cards().find((c) => c.querySelector(".camera-name").textContent === name);
  const badges = (c) => Array.from(c.querySelectorAll(".device-badge")).map((b) => b.textContent);
  const live = (topic) => sources.filter((s) => !s.closed && s.url.includes("/api/captions-stream") && s.url.includes(encodeURIComponent(topic)));

  console.log("== connect: one card per camera");
  document.getElementById("connect-btn").click();
  await tick(150);
  check("three cards", cards().length === 3, cards().map((c) => c.querySelector(".camera-name").textContent).join(", "));
  const front = card("front camera");
  check("card shows detection device and VLM device badges", front && JSON.stringify(badges(front)) === JSON.stringify(["NPU", "VLM GPU"]), front && badges(front).join(" + "));
  check("caption panel is visible for a captioning camera", front && !front.querySelector(".caption-panel").hidden);
  check("each camera listens to its own caption topic",
        ["front", "bottom", "back"].every((n) => live(`uav/caption/${n}_camera`).length === 1));
  check("captions request carries the topic, not a device", sources.some((s) => /captions-stream\?host=[^&]+&topic=uav%2Fcaption%2Ffront_camera/.test(s.url)));
  check("relay is requested per camera stream path", requests.some((u) => u.includes("/api/relay-path") && u.includes("camera=uav-front-camera")));
  check("card tooltip lists models, prompt, overlay and video",
        /captions GPU InternVL2-1B/.test(front.querySelector(".device-card-meta").title) && /prompt: Describe/.test(front.querySelector(".device-card-meta").title)
        && /overlay on/.test(front.querySelector(".device-card-meta").title) && /uav_sample_front\.avi/.test(front.querySelector(".device-card-meta").title));
  check("no request still uses the old device parameter", !requests.some((u) => /[?&]device=/.test(u)));

  console.log("== the front camera's pipeline is replaced by detection only with the overlay off");
  cameras = [camera("front", { pipeline_name: "uav_object_detection_cpu", detection_device: "cpu", captioning: false, vlm_device: null, vlm_model: null, prompt: null, mqtt_topic: null, overlay: false }), cameras[1], cameras[2]];
  await pollTimer();
  await tick(150);
  const front2 = card("front camera");
  check("badges follow the new pipeline", JSON.stringify(badges(front2)) === JSON.stringify(["CPU", "no overlay"]), badges(front2).join(" + "));
  check("caption panel is hidden", front2.querySelector(".caption-panel").hidden);
  check("its caption subscription is closed, the others stay", live("uav/caption/front_camera").length === 0 && live("uav/caption/bottom_camera").length === 1 && live("uav/caption/back_camera").length === 1);

  console.log("== it switches back to captioning");
  cameras[0] = camera("front");
  await pollTimer();
  await tick(150);
  check("caption panel and subscription return", !card("front camera").querySelector(".caption-panel").hidden && live("uav/caption/front_camera").length === 1);

  console.log("== bottom camera stops, a left camera starts");
  cameras = [cameras[0], cameras[2], camera("left", { captioning: false, vlm_device: null, vlm_model: null, mqtt_topic: null })];
  await pollTimer();
  await tick(150);
  const names = cards().map((c) => c.querySelector(".camera-name").textContent).sort();
  check("card set follows the running cameras", JSON.stringify(names) === JSON.stringify(["back camera", "front camera", "left camera"]), names.join(", "));
  check("the stopped camera no longer listens for captions", live("uav/caption/bottom_camera").length === 0);
  check("a detection-only camera has no caption subscription", live("uav/caption/left_camera").length === 0 && card("left camera").querySelector(".caption-panel").hidden);

  console.log("== a live camera (RealSense) next to the video cameras");
  cameras = [cameras[0], camera("realsense", { video: null, device: "/dev/v4l/by-id/usb-Intel_RealSense-video-index4", camera_size: "1280x720", captioning: false, vlm_device: null, vlm_model: null, mqtt_topic: null, pipeline_name: "uav_camera_npu", detection_device: "npu" }), cameras[1]];
  await pollTimer();
  await tick(150);
  const rs = card("realsense camera");
  check("live camera card has a LIVE badge with the device in the tooltip",
        rs && JSON.stringify(badges(rs)) === JSON.stringify(["NPU", "LIVE"]) && /usb-Intel_RealSense-video-index4 \(1280x720\)/.test(rs.querySelector(".device-badge-live").title), rs && badges(rs).join(" + "));
  check("its tooltip names the device, not a video file", /live camera \/dev\/v4l\/by-id\/usb-Intel_RealSense-video-index4 1280x720/.test(rs.querySelector(".device-card-meta").title) && !/video uav_sample/.test(rs.querySelector(".device-card-meta").title));
  cameras[0] = camera("front", { video: null, rtsp_url: "rtsp://host.docker.internal:8554/uav-1/nadir" });
  await pollTimer();
  await tick(150);
  check("an RTSP camera's tooltip names its stream", /stream rtsp:\/\/host\.docker\.internal:8554\/uav-1\/nadir/.test(card("front camera").querySelector(".device-card-meta").title));
  cameras[0] = camera("front");
  await pollTimer();
  await tick(150);
  check("video cameras have no LIVE badge", !badges(card("front camera")).includes("LIVE"));
  check("a live camera without captions has no caption subscription", card("realsense camera").querySelector(".caption-panel").hidden && live("uav/caption/realsense_camera").length === 0);

  console.log("== a live camera with captions (uav_camera_npu_gpu)");
  cameras = [cameras[0], camera("realsense", { video: null, device: "/dev/video4", camera_size: "1280x720", pipeline_name: "uav_camera_npu_gpu", detection_device: "npu", mqtt_topic: "uav/caption/realsense_camera" }), cameras[2]];
  await pollTimer();
  await tick(150);
  const rsc = card("realsense camera");
  check("captioned live camera shows NPU, VLM GPU and LIVE badges", rsc && JSON.stringify(badges(rsc)) === JSON.stringify(["NPU", "VLM GPU", "LIVE"]), rsc && badges(rsc).join(" + "));
  check("its caption panel is visible", rsc && !rsc.querySelector(".caption-panel").hidden);
  check("it listens to its own caption topic", live("uav/caption/realsense_camera").length === 1);

  console.log("== host unreachable keeps the cards");
  reachable = false;
  await pollTimer();
  await tick(100);
  check("cards stay on a transient failure", cards().length === 3);

  console.log(failures === 0 ? "=== all checks passed" : `=== ${failures} check(s) failed`);
  window.close();
  process.exit(failures === 0 ? 0 : 1);
}
main().catch((err) => { console.error(err); process.exit(2); });
