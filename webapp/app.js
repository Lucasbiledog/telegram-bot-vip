// ./webapp/app.js

// --- helpers de DOM ---
const $ = (id) => document.getElementById(id);
const alertBox = $("alert");
const ctxInfo = $("ctxInfo");


function showAlert(html, ok = false) {
  alertBox.innerHTML = `<div class="${ok ? "ok" : "error"}">${html}</div>`;
}

function clearAlert() {
  alertBox.innerHTML = "";
}

// --- pega uid/ts/sig da query ---
const q = new URLSearchParams(location.search);
const uid = q.get("uid");
const ts  = q.get("ts");
const sig = q.get("sig");

// --- render de planos ---
function renderPlans(plansObj) {
  const el = $("plans");
  el.innerHTML = "";
  // Espera { "30": 19.99, "90": 49.99, ... }
  const entries = Object.entries(plansObj || {}).map(([days, price]) => [Number(days), Number(price)]);
  // ordenar por dias crescente
  entries.sort((a, b) => a[0] - b[0]);
  for (const [days, price] of entries) {
    const pill = document.createElement("div");
    pill.className = "pill";
    pill.innerHTML = `<div><b>${days} dias</b></div><div class="muted">$${price.toFixed(2)}</div>`;
    el.appendChild(pill);
  }
  if (!entries.length) {
    el.innerHTML = `<div class="muted">Nenhum plano configurado.</div>`;
  }
}

// --- carrega carteira + planos do backend ---
async function loadConfig() {
  if (!uid || !ts || !sig) {
    showAlert("Link sem parâmetros de segurança (uid/ts/sig). Abra esta página pelo botão /checkout no Telegram.", false);
    return;
  }
  try {
    const r = await fetch(`/api/config?uid=${encodeURIComponent(uid)}&ts=${encodeURIComponent(ts)}&sig=${encodeURIComponent(sig)}`);
    if (!r.ok) {
      const t = await r.text().catch(() => "");
      throw new Error(`Falha ao carregar config (${r.status}) ${t || ""}`);
    }
    const j = await r.json();
    $("addr").value = j.wallet || "";
    renderPlans(j.plans_usd || {});
    // Mensagens contextuais opcionais
    if (ctxInfo) {
      const parts = [];
      if (Array.isArray(j.networks) && j.networks.length) {
        parts.push(`Redes suportadas: ${j.networks.join(", ")}`);
      }
      if (j.confirmations_min) {
        parts.push(`Confirmação mínima: ${j.confirmations_min}`);
      }
      if (parts.length) {
        ctxInfo.textContent = parts.join(" • ");
        ctxInfo.style.display = "block";
      }
    }
    if (!j.wallet) {
      showAlert("Carteira não configurada no servidor.", false);
    }
  } catch (err) {
    console.error(err);
    showAlert("Erro ao carregar configurações. Tente abrir o /checkout novamente.", false);
  }
}

// --- validar pagamento (POST /api/validate) ---
async function validatePayment() {
  clearAlert();
  const hash = $("txhash").value.trim();
  if (!hash) {
    showAlert("Informe o hash da transação (ex.: 0xabc...)", false);
    return;
  }
  if (!uid) {
    showAlert("UID ausente. Abra esta página pelo botão /checkout no Telegram.", false);
    return;
  }

  const btn = $("validarBtn");
  const pasteBtn = $("pasteBtn");
  btn.disabled = true;
  pasteBtn.disabled = true;
  btn.textContent = "Validando…";

  try {
    const r = await fetch("/api/validate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ uid: Number(uid), username: null, hash }),
    });

    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      showAlert(`Erro ${r.status}: ${j.detail || "Falha na validação"}`, false);
      return;
    }

    if (j.ok) {
      // mostra mensagem e redireciona para o convite se existir
      showAlert(j.message || "Pagamento confirmado!", true);

      if (j.invite) {
        // redireciona imediatamente
        setTimeout(() => {
          window.location.href = j.invite;
        }, 600); // pequeno delay para o usuário ver a mensagem
      } else {
        showAlert((j.message || "Pagamento confirmado!") + "<br><br>Não recebemos o link de convite. Tente novamente.", true);
      }
    } else {
      showAlert(j.message || "Pagamento não reconhecido.", false);
    }
  } catch (err) {
    console.error(err);
    showAlert("Erro de rede. Tente novamente em alguns segundos.", false);
  } finally {
    btn.disabled = false;
    pasteBtn.disabled = false;
    btn.textContent = "Validar pagamento";
  }
}

// --- eventos ---
$("pasteBtn").addEventListener("click", async () => {
  try {
    const t = await navigator.clipboard.readText();
    if (t) $("txhash").value = t.trim();
  } catch (e) {
    console.warn("Clipboard read falhou:", e);
  }
});

$("validarBtn").addEventListener("click", validatePayment);

// --- heartbeat p/ manter Render ativo + log no console ---
console.log("[checkout] page loaded", { uid, ts });
setInterval(() => {
  console.log("[heartbeat] page alive", new Date().toISOString());
  fetch("/keepalive").catch(() => {});
}, 60_000);

// start
loadConfig();
