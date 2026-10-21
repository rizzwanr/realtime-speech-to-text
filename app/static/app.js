const MODE_LABELS = {
  auto: "Auto",
  hinglish: "Hindi + English",
  hi: "Hindi",
  en: "English",
};

const state = {
  recording: false,
  stopping: false,
  geminiConfigured: false,
  committed: [],
  interim: "",
  detected: [],
  ws: null,
  recognition: null,
  audio: null,
  startedAt: 0,
  timer: null,
  meterFrame: 0,
  offset: 0,
  limit: 10,
  total: 0,
  query: "",
  editingId: null,
  pendingDeleteId: null,
};

const $ = (selector) => document.querySelector(selector);

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2800);
}

function selectedMode() {
  return document.querySelector('input[name="language"]:checked')?.value || "auto";
}

function browserLanguage(mode) {
  return mode === "hi" ? "hi-IN" : "en-IN";
}

function fullText() {
  return [...state.committed, state.interim].map((part) => part.trim()).filter(Boolean).join(" ").trim();
}

function formatClock(ms) {
  const total = Math.floor(ms / 1000);
  const minutes = String(Math.floor(total / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function formatWhen(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function sourceLabel(source) {
  if (!source || source === "manual") return "Manual";
  if (source.includes("browser")) return "Browser";
  if (source.includes("gemini") || source.includes("transcribe")) return "Gemini Live";
  return source;
}

function noteLanguage(code) {
  if (code && !state.detected.includes(code)) state.detected.push(code);
  renderLanguages();
}

function renderLanguages() {
  const chips = $("#lang-chips");
  chips.replaceChildren();
  state.detected.forEach((code) => chips.append(Object.assign(document.createElement("span"), { textContent: code })));
}

function renderTranscript() {
  const box = $("#transcript");
  box.replaceChildren();
  if (!state.committed.length && !state.interim) {
    box.append(Object.assign(document.createElement("p"), {
      className: "placeholder",
      textContent: "Your words will show up here while you speak.",
    }));
  }
  state.committed.forEach((text) => {
    box.append(Object.assign(document.createElement("p"), { textContent: text }));
  });
  if (state.interim) {
    box.append(Object.assign(document.createElement("p"), { className: "interim", textContent: state.interim }));
  }
  const hasText = Boolean(fullText());
  $("#copy-btn").disabled = !hasText;
  $("#discard-btn").disabled = !state.recording && !hasText;
  box.scrollTop = box.scrollHeight;
}

function setRecordingUI(active) {
  state.recording = active;
  $("#record-btn").setAttribute("aria-pressed", String(active));
  $("#record-label").textContent = active ? "Stop" : "Record";
  $("#record-state").textContent = active ? "Listening" : "Microphone is idle";
  document.querySelectorAll('input[name="language"], #vocab, #session-title').forEach((node) => {
    node.disabled = active;
  });
  document.title = active ? "Listening — Vaani" : "Vaani — Speech to Text";
  document.body.classList.toggle("browser-live", active && !state.geminiConfigured);
  $("#discard-btn").disabled = !active && !fullText();
  if (active) {
    state.startedAt = Date.now();
    state.timer = setInterval(() => {
      $("#timer").textContent = formatClock(Date.now() - state.startedAt);
    }, 250);
  } else if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

function updateModeHint() {
  const mode = selectedMode();
  const hint = $("#mode-hint");
  if (state.geminiConfigured) {
    hint.textContent = mode === "auto"
      ? "Gemini detects the language as you speak, including Hindi and English in the same sentence."
      : `Gemini is biased toward ${MODE_LABELS[mode]}.`;
    return;
  }
  hint.textContent = mode === "hi"
    ? "Browser recognition is listening in Hindi. Add a Gemini API key for mixed Hindi and English in one sentence."
    : "Browser recognition is listening in Indian English. Mixed Hindi–English code-switching needs Gemini Live.";
}

async function refreshHealth() {
  const health = await fetch("/api/health").then((response) => response.json());
  state.geminiConfigured = Boolean(health.geminiConfigured);
  const engine = state.geminiConfigured ? `Gemini Live · ${health.model}` : "Browser speech recognition";
  $("#engine-note").textContent = engine;
  $("#engine-pill").textContent = state.geminiConfigured ? "Gemini Live" : "Browser speech";
  $("#db-note").textContent = health.database === "postgresql" ? "History stored in PostgreSQL" : `History stored in ${health.database}`;
  updateModeHint();
  const stats = await fetch("/api/stats").then((response) => response.json());
  $("#nav-count").textContent = String(stats.total || 0);
}

function wsUrl() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${location.host}/ws/transcribe`;
}

async function stopAudio() {
  const audio = state.audio;
  state.audio = null;
  cancelAnimationFrame(state.meterFrame);
  $("#meter-bar").style.transform = "scaleX(0.08)";
  if (audio?.node) {
    audio.node.port.postMessage("flush");
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  audio?.stream?.getTracks().forEach((track) => track.stop());
  if (audio?.audioContext) await audio.audioContext.close().catch(() => {});
}

function watchLevel(analyser) {
  const samples = new Uint8Array(analyser.fftSize);
  const tick = () => {
    if (!state.audio) return;
    analyser.getByteTimeDomainData(samples);
    let energy = 0;
    samples.forEach((value) => {
      const centered = (value - 128) / 128;
      energy += centered * centered;
    });
    const level = Math.min(1, Math.sqrt(energy / samples.length) * 5);
    $("#meter-bar").style.transform = `scaleX(${0.08 + level})`;
    state.meterFrame = requestAnimationFrame(tick);
  };
  tick();
}

async function openMicrophone() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const audioContext = new AudioContext();
  await audioContext.resume();
  const source = audioContext.createMediaStreamSource(stream);
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  state.audio = { stream, audioContext, source, analyser };
  watchLevel(analyser);
  return state.audio;
}

async function startGemini() {
  await openMicrophone();
  await state.audio.audioContext.audioWorklet.addModule("/static/pcm-worklet.js");
  const node = new AudioWorkletNode(state.audio.audioContext, "pcm-downsampler");
  const mute = state.audio.audioContext.createGain();
  mute.gain.value = 0;
  state.audio.source.connect(node);
  node.connect(mute);
  mute.connect(state.audio.audioContext.destination);
  state.audio.node = node;

  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  const queue = [];
  let ready = false;

  await new Promise((resolve, reject) => {
    let settled = false;
    const fail = (message) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error(message));
    };
    const succeed = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      resolve();
    };
    const timeout = setTimeout(() => fail("Gemini Live took too long to start."), 12000);
    ws.onerror = () => fail("Could not open the transcription socket.");
    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: "start",
        languageMode: selectedMode(),
        title: $("#session-title").value,
        customVocabulary: $("#vocab").value,
      }));
    };
    node.port.onmessage = (event) => {
      if (!ready) queue.push(event.data);
      else if (ws.readyState === WebSocket.OPEN) ws.send(event.data);
    };
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "ready") {
        ready = true;
        while (queue.length && ws.readyState === WebSocket.OPEN) ws.send(queue.shift());
        succeed();
        return;
      }
      handleGeminiMessage(message, ready, fail);
    };
  });
  setRecordingUI(true);
}

function handleGeminiMessage(message, ready, fail) {
  if (message.type === "interim") {
    state.interim = message.text || "";
    noteLanguage(message.languageCode);
    renderTranscript();
  } else if (message.type === "final") {
    if (message.text) state.committed.push(message.text);
    state.interim = "";
    noteLanguage(message.languageCode);
    renderTranscript();
  } else if (message.type === "saved") {
    toast("Saved to history.");
    refreshHealth().catch(() => {});
    if (!$("#view-history").hidden) loadHistory().catch(() => {});
  } else if (message.type === "empty") {
    toast("Nothing to save yet.");
  } else if (message.type === "discarded") {
    toast("Session discarded.");
  } else if (message.type === "error") {
    if (!ready && fail) fail(message.message || "Gemini Live failed.");
    else toast(message.message || "Transcription error.");
  }
}

async function startBrowser() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    throw new Error("This browser has no speech recognition. Use Chrome or Edge, or configure Gemini Live.");
  }
  const recognition = new Recognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = browserLanguage(selectedMode());
  state.detected = [recognition.lang];
  renderLanguages();
  recognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const piece = event.results[index][0].transcript;
      if (event.results[index].isFinal) state.committed.push(piece.trim());
      else interim += piece;
    }
    state.interim = interim;
    renderTranscript();
  };
  recognition.onerror = (event) => {
    if (event.error === "not-allowed") toast("Microphone permission was blocked.");
    else if (event.error !== "aborted" && event.error !== "no-speech") toast(`Speech recognition: ${event.error}`);
  };
  recognition.onend = () => {
    if (state.recording && !state.stopping) recognition.start();
  };
  recognition.start();
  state.recognition = recognition;
  setRecordingUI(true);
}

async function startRecording() {
  if (state.recording) {
    await stopRecording(true);
    return;
  }
  state.committed = [];
  state.interim = "";
  state.detected = [];
  state.stopping = false;
  renderLanguages();
  renderTranscript();
  try {
    if (state.geminiConfigured) {
      try {
        await startGemini();
        return;
      } catch (error) {
        await stopAudio();
        if (state.ws) {
          state.ws.close();
          state.ws = null;
        }
        toast(`${error.message} Using browser recognition instead.`);
      }
    }
    await startBrowser();
  } catch (error) {
    await stopAudio();
    setRecordingUI(false);
    toast(error.message || "Could not start listening.");
  }
}

async function stopRecording(save) {
  state.stopping = true;
  const recognition = state.recognition;
  const socket = state.ws;
  state.recognition = null;
  if (recognition) {
    recognition.onend = null;
    recognition.stop();
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  await stopAudio();
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "stop", save }));
  } else if (recognition && save) {
    await saveBrowserTranscript();
  }
  if (!save) {
    state.committed = [];
    state.interim = "";
    state.detected = [];
    renderLanguages();
    renderTranscript();
    if (!(socket && socket.readyState === WebSocket.OPEN)) toast("Session discarded.");
  }
  state.ws = null;
  setRecordingUI(false);
  state.stopping = false;
}

async function saveBrowserTranscript() {
  const transcript = fullText();
  if (!transcript) {
    toast("Nothing to save yet.");
    return;
  }
  const response = await fetch("/api/transcriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: $("#session-title").value,
      transcript,
      language_mode: selectedMode(),
      detected_languages: state.detected,
      source: "browser-speech-recognition",
    }),
  });
  if (!response.ok) {
    toast("Could not save the transcript.");
    return;
  }
  toast("Saved to history.");
  await refreshHealth();
}

async function copyTranscript() {
  const text = fullText();
  if (!text) return;
  await navigator.clipboard.writeText(text);
  toast("Copied.");
}

function showView(name) {
  $("#view-live").hidden = name !== "live";
  $("#view-history").hidden = name !== "history";
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  if (name === "history") loadHistory().catch(() => toast("Could not load history."));
}

async function loadHistory() {
  const params = new URLSearchParams({
    q: state.query,
    limit: String(state.limit),
    offset: String(state.offset),
  });
  const payload = await fetch(`/api/transcriptions?${params}`).then((response) => response.json());
  state.total = payload.total || 0;
  const list = $("#history-list");
  list.replaceChildren();
  const from = state.total === 0 ? 0 : state.offset + 1;
  const to = Math.min(state.offset + state.limit, state.total);
  $("#result-count").textContent = state.total ? `Showing ${from}–${to} of ${state.total}` : "No saved transcriptions";
  $("#prev-page").disabled = state.offset === 0;
  $("#next-page").disabled = state.offset + state.limit >= state.total;
  if (!payload.items.length) {
    list.append(Object.assign(document.createElement("p"), {
      className: "empty",
      textContent: "Record a session, or create one manually. It will show up here.",
    }));
    return;
  }
  payload.items.forEach((item) => list.append(renderHistoryItem(item)));
}

function renderHistoryItem(item) {
  const row = document.createElement("article");
  row.className = "history-item";
  const title = document.createElement("div");
  title.append(Object.assign(document.createElement("h3"), { textContent: item.title }));
  title.append(Object.assign(document.createElement("p"), {
    className: "hint",
    textContent: formatWhen(item.updated_at),
  }));
  const preview = document.createElement("p");
  preview.className = "preview";
  preview.textContent = item.transcript.length > 140 ? `${item.transcript.slice(0, 137)}...` : item.transcript;
  const language = document.createElement("p");
  language.textContent = MODE_LABELS[item.language_mode] || item.language_mode;
  const source = document.createElement("p");
  source.className = "source";
  source.textContent = sourceLabel(item.source);
  const actions = document.createElement("div");
  actions.className = "item-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "ghost";
  edit.textContent = "Edit";
  edit.addEventListener("click", () => openEditor(item.id));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => askDelete(item.id, item.title));
  actions.append(edit, remove);
  row.append(title, preview, language, source, actions);
  return row;
}

function openEditor(id) {
  state.editingId = id;
  $("#editor-delete").hidden = !id;
  $("#editor-heading").textContent = id ? "Edit transcription" : "New transcription";
  if (!id) {
    $("#editor-title").value = "";
    $("#editor-transcript").value = "";
    $("#editor-mode").value = "auto";
    $("#editor-dialog").showModal();
    return;
  }
  fetch(`/api/transcriptions/${id}`)
    .then((response) => {
      if (!response.ok) throw new Error("missing");
      return response.json();
    })
    .then((item) => {
      $("#editor-title").value = item.title;
      $("#editor-transcript").value = item.transcript;
      $("#editor-mode").value = item.language_mode;
      $("#editor-dialog").showModal();
    })
    .catch(() => toast("That transcription is no longer available."));
}

async function saveEditor(event) {
  event.preventDefault();
  const payload = {
    title: $("#editor-title").value,
    transcript: $("#editor-transcript").value,
    language_mode: $("#editor-mode").value,
  };
  const id = state.editingId;
  const response = await fetch(id ? `/api/transcriptions/${id}` : "/api/transcriptions", {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(id ? payload : { ...payload, source: "manual" }),
  });
  if (!response.ok) {
    toast("Could not save. Check the title and transcript.");
    return;
  }
  $("#editor-dialog").close();
  toast("Saved.");
  await refreshHealth();
  await loadHistory();
}

function askDelete(id, title) {
  state.pendingDeleteId = id;
  $("#confirm-copy").textContent = title
    ? `“${title}” will be removed from the history.`
    : "This removes it from the history.";
  $("#confirm-dialog").showModal();
}

async function confirmDelete() {
  const id = state.pendingDeleteId;
  $("#confirm-dialog").close();
  if (!id) return;
  const response = await fetch(`/api/transcriptions/${id}`, { method: "DELETE" });
  if (!response.ok && response.status !== 204) {
    toast("Could not delete that transcription.");
    return;
  }
  if (state.editingId === id) $("#editor-dialog").close();
  toast("Deleted.");
  if (state.offset >= state.total - 1 && state.offset > 0) state.offset -= state.limit;
  await refreshHealth();
  await loadHistory();
}

document.querySelectorAll(".nav-btn").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});
$("#record-btn").addEventListener("click", () => startRecording().catch((error) => toast(error.message)));
$("#discard-btn").addEventListener("click", () => stopRecording(false).catch((error) => toast(error.message)));
$("#copy-btn").addEventListener("click", () => copyTranscript().catch(() => toast("Could not copy.")));
$("#new-btn").addEventListener("click", () => openEditor(null));
$("#editor-form").addEventListener("submit", (event) => saveEditor(event).catch(() => toast("Could not save.")));
$("#editor-cancel").addEventListener("click", () => $("#editor-dialog").close());
$("#editor-delete").addEventListener("click", () => askDelete(state.editingId, $("#editor-title").value));
$("#confirm-no").addEventListener("click", () => $("#confirm-dialog").close());
$("#confirm-yes").addEventListener("click", () => confirmDelete().catch(() => toast("Could not delete.")));
$("#search").addEventListener("input", (event) => {
  state.query = event.target.value;
  state.offset = 0;
  clearTimeout($("#search").timer);
  $("#search").timer = setTimeout(() => loadHistory().catch(() => {}), 200);
});
$("#prev-page").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  loadHistory().catch(() => {});
});
$("#next-page").addEventListener("click", () => {
  if (state.offset + state.limit < state.total) {
    state.offset += state.limit;
    loadHistory().catch(() => {});
  }
});
document.querySelectorAll('input[name="language"]').forEach((input) => {
  input.addEventListener("change", updateModeHint);
});

renderTranscript();
refreshHealth().catch(() => {
  $("#engine-note").textContent = "The service is not responding.";
});
