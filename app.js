const API_BASE = "https://mathsnap-1vc4.onrender.com";

const fileInput = document.getElementById("fileInput");
const dropzone = document.getElementById("dropzone");
const previewWrap = document.getElementById("previewWrap");
const preview = document.getElementById("preview");
const removeBtn = document.getElementById("removeBtn");
const solveBtn = document.getElementById("solveBtn");
const solveLabel = document.getElementById("solveLabel");
const spinner = document.getElementById("spinner");
const demoBtn = document.getElementById("demoBtn");
const emptyState = document.getElementById("emptyState");
const result = document.getElementById("result");
const errorBox = document.getElementById("error");
const status = document.getElementById("status");
const question = document.getElementById("question");
const topic = document.getElementById("topic");
const steps = document.getElementById("steps");
const answer = document.getElementById("answer");
const verification = document.getElementById("verification");

let selectedFile = null;

function setStatus(label, mode = "") {
  status.className = "status " + mode;
  status.innerHTML = `<span></span> ${label}`;
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  result.classList.add("hidden");
  emptyState.classList.add("hidden");
  setStatus("Needs attention");
}

function clearError() {
  errorBox.classList.add("hidden");
}

function setFile(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) {
    showError("Please choose an image file.");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showError("That image is larger than 10 MB.");
    return;
  }

  selectedFile = file;
  preview.src = URL.createObjectURL(file);
  previewWrap.classList.remove("hidden");
  dropzone.classList.add("hidden");
  solveBtn.disabled = false;
  clearError();
  setStatus("Image ready");
}

function resetUpload() {
  selectedFile = null;
  fileInput.value = "";
  preview.src = "";
  previewWrap.classList.add("hidden");
  dropzone.classList.remove("hidden");
  solveBtn.disabled = true;
  setStatus("Ready");
}

function renderResult(data) {
  question.textContent = data.question || "Problem not detected";
  topic.textContent = data.topic || "Math";
  answer.textContent = data.answer || "No final answer returned";

  steps.innerHTML = "";
  (data.steps || []).forEach((step, index) => {
    const card = document.createElement("article");
    card.className = "step";

    const head = document.createElement("div");
    head.className = "step-head";
    head.textContent = step.title || `Step ${index + 1}`;

    const explanation = document.createElement("div");
    explanation.className = "step-explanation";
    explanation.textContent = step.explanation || "";

    card.append(head, explanation);

    if (step.latex) {
      const math = document.createElement("div");
      math.className = "math";
      math.textContent = step.latex;
      card.appendChild(math);
    }

    steps.appendChild(card);
  });

  const v = data.verification || {};
  if (v.status === "verified") {
    verification.textContent = "✓ Independently checked · " + v.message;
    setStatus("Verified", "good");
  } else if (v.status === "failed") {
    verification.textContent = "⚠ Verification failed · " + v.message;
    setStatus("Check result");
  } else {
    verification.textContent = "• " + (v.message || "Independent check unavailable");
    setStatus("Solved");
  }

  emptyState.classList.add("hidden");
  errorBox.classList.add("hidden");
  result.classList.remove("hidden");
}

async function solve() {
  if (!selectedFile) return;

  solveBtn.disabled = true;
  spinner.classList.remove("hidden");
  solveLabel.textContent = "Reading & solving…";
  setStatus("Working", "busy");
  clearError();

  const form = new FormData();
  form.append("file", selectedFile);

  try {
    const response = await fetch(`${API_BASE}/api/solve`, {
      method: "POST",
      body: form
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.detail || `Server returned ${response.status}`);
    }

    renderResult(data);
  } catch (err) {
    showError(err.message || "Something went wrong.");
  } finally {
    solveBtn.disabled = !selectedFile;
    spinner.classList.add("hidden");
    solveLabel.textContent = "Solve problem";
  }
}

function demo() {
  const data = {
    question: "2x² + 5x − 3 = 0",
    topic: "Algebra · Quadratic",
    steps: [
      { title: "Step 1 · Identify a, b and c", explanation: "For ax² + bx + c = 0, the coefficients are a = 2, b = 5, c = −3.", latex: "a = 2,\\quad b = 5,\\quad c = -3" },
      { title: "Step 2 · Factor the quadratic", explanation: "Find two factors whose product is −6 and whose middle terms combine to 5x.", latex: "(2x-1)(x+3)=0" },
      { title: "Step 3 · Set each factor to zero", explanation: "A product is zero when at least one factor is zero.", latex: "2x-1=0\\quad\\text{or}\\quad x+3=0" },
      { title: "Step 4 · Solve", explanation: "Solve each linear equation.", latex: "x=\\frac12\\quad\\text{or}\\quad x=-3" }
    ],
    answer: "x = 1/2 or x = −3",
    verification: { status: "verified", message: "Substitution confirms both roots satisfy the equation." }
  };
  renderResult(data);
}

fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
removeBtn.addEventListener("click", resetUpload);
solveBtn.addEventListener("click", solve);
demoBtn.addEventListener("click", demo);

["dragenter", "dragover"].forEach(type => dropzone.addEventListener(type, e => {
  e.preventDefault();
  dropzone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(type => dropzone.addEventListener(type, e => {
  e.preventDefault();
  dropzone.classList.remove("dragging");
}));
dropzone.addEventListener("drop", e => setFile(e.dataTransfer.files[0]));
