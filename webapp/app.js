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

// --- pega uid/ts/sig/username da query ---
const q = new URLSearchParams(location.search);
let uid = q.get("uid");
const ts  = q.get("ts");
const sig = q.get("sig");
const username = q.get("username");

// --- fallback para obter UID via Telegram WebApp ---
if (!uid && window.Telegram && window.Telegram.WebApp) {
  try {
    const webApp = window.Telegram.WebApp;
    if (webApp.initDataUnsafe && webApp.initDataUnsafe.user) {
      uid = webApp.initDataUnsafe.user.id.toString();
      console.log("[telegram-webapp] UID obtido via WebApp:", uid);
    }
  } catch (e) {
    console.warn("[telegram-webapp] Falha ao obter UID:", e);
  }
}

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
  // Remover verificação de segurança - acesso direto permitido
  // if (!uid || !ts || !sig) {
  //   loadBasicInfo();
  //   return;
  // }
  try {
    // Carregar configurações sem autenticação
    let configUrl = "/api/config";
    if (uid && ts && sig) {
      configUrl = `/api/config?uid=${encodeURIComponent(uid)}&ts=${encodeURIComponent(ts)}&sig=${encodeURIComponent(sig)}`;
    }
    
    const r = await fetch(configUrl);
    if (!r.ok) {
      // Fallback para configurações padrão se a API falhar
      loadBasicInfo();
      return;
    }
    const j = await r.json();
    $("addr").value = j.wallet || "";
    renderPlans(j.plans_usd || {});
    
    // Preencher automaticamente o campo de user ID se disponível
    if (uid && $("userid")) {
      $("userid").value = uid;
      $("userid").disabled = true; // Desabilitar edição quando vem da URL
      $("userid").style.background = "#16a34a20";
      $("userid").style.borderColor = "#16a34a";
      
      // Atualizar status
      const statusEl = $("userid-status");
      if (statusEl) {
        statusEl.innerHTML = "✅ ID capturado automaticamente do Telegram. VIP será ativado automaticamente!";
        statusEl.style.color = "#16a34a";
      }
      
      console.log("[auto-fill] User ID preenchido automaticamente:", uid);
    }
    
    // Mensagens contextuais opcionais
    if (ctxInfo) {
      const parts = [];
      if (Array.isArray(j.networks) && j.networks.length) {
        parts.push(`Redes suportadas: ${j.networks.join(", ")}`);
      }
      if (j.confirmations_min) {
        parts.push(`Confirmação mínima: ${j.confirmations_min}`);
      }
      if (username) {
        parts.push(`Usuário: @${username}`);
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
let isValidating = false; // Flag para prevenir duplo clique
async function validatePayment() {
  if (isValidating) {
    console.log("Validação já em andamento, ignorando clique...");
    return;
  }
  
  isValidating = true;
  clearAlert();
  const hash = $("txhash").value.trim();
  if (!hash) {
    showAlert("Informe o hash da transação (ex.: 0xabc...)", false);
    isValidating = false;
    return;
  }
  
  // Usar UID se disponível, caso contrário usar ID fornecido pelo usuário ou valor padrão
  let userID = uid;
  if (!userID) {
    const userInput = $("userid")?.value?.trim();
    if (userInput && !isNaN(userInput)) {
      userID = userInput;
    } else {
      // Gerar um ID temporário baseado no hash para permitir validação
      userID = "temp_" + Math.abs(hash.split('').reduce((a,b) => (((a << 5) - a) + b.charCodeAt(0))|0, 0));
    }
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
      body: JSON.stringify({ uid: userID, username: null, hash }),
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
    isValidating = false; // Reset da flag
  }
}

// --- eventos ---
$("pasteBtn").addEventListener("click", async (e) => {
  e.preventDefault();
  e.stopPropagation();
  try {
    const t = await navigator.clipboard.readText();
    if (t) $("txhash").value = t.trim();
  } catch (e) {
    console.warn("Clipboard read falhou:", e);
  }
});

$("validarBtn").addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  console.log("[click] Botão validar clicado, iniciando validação...");
  validatePayment();
});

// --- heartbeat p/ manter Render ativo + log no console ---
console.log("[checkout] page loaded", { uid, ts, sig, username });
console.log("[checkout] User auto-detected:", uid ? "✅ YES" : "❌ NO");
console.log("[checkout] Telegram WebApp available:", !!window.Telegram?.WebApp);
if (window.Telegram?.WebApp) {
  console.log("[checkout] WebApp user:", window.Telegram.WebApp.initDataUnsafe?.user);
}
setInterval(() => {
  console.log("[heartbeat] page alive", new Date().toISOString());
  fetch("/keepalive").catch(() => {});
}, 60_000);

// --- função para mostrar como descobrir o ID ---
function showHowToGetId() {
  showAlert(`
    <h3>Como descobrir seu ID do Telegram</h3>
    <ol>
      <li>Abra o Telegram</li>
      <li>Procure pelo bot <code>@userinfobot</code></li>
      <li>Inicie uma conversa com ele</li>
      <li>Ele enviará seu ID automaticamente</li>
    </ol>
    <p><strong>Importante:</strong> Sem o ID correto, você não receberá o convite do grupo VIP automaticamente.</p>
  `, true);
}

// --- carrega informações básicas sem autenticação ---
async function loadBasicInfo() {
  try {
    // Mostrar carteira padrão (pode ser obtida da API)
    $("addr").value = "0x40dDBD27F878d07808339F9965f013F1CBc2F812";
    
    // Mostrar planos padrão
    const defaultPlans = {
      "30": 0.05,
      "60": 1.00,
      "180": 1.50,
      "365": 2.00
    };
    renderPlans(defaultPlans);
    
    // Página totalmente funcional
    showAlert(`
      <h3>✅ Página de pagamento independente</h3>
      <p>Esta página funciona completamente sem o bot do Telegram.</p>
      <p>Para receber o convite do grupo VIP, insira seu ID do Telegram no campo acima.</p>
    `, true);
    
  } catch (err) {
    console.warn("Erro ao carregar info básica:", err);
  }
}

// start
loadConfig();
