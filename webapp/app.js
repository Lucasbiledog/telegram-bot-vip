(async function () {
  const $ = (sel) => document.querySelector(sel);
  const alertBox = $("#alert");
  const addr = $("#addr");
  const plans = $("#plans");
  const hashInput = $("#txhash");

  const showError = (msg) => {
    alertBox.innerHTML = `<div class="error">${msg || "Erro ao validar. Tente novamente."}</div>`;
  };
  const showOk = (msg) => {
    alertBox.innerHTML = `<div class="ok">${msg}</div>`;
  };

  // carrega config backend
  try {
    const r = await fetch("/api/config");
    if (!r.ok) throw 0;
    const cfg = await r.json();
    addr.value = cfg.wallet || "—";
    if (cfg.prices_usd) {
      plans.innerHTML = Object.entries(cfg.prices_usd)
        .sort((a, b) => parseInt(a[0]) - parseInt(b[0]))
        .map(([d, p]) => `<div class="pill"><div style="font-weight:700">${d} dias</div><div class="muted">$${Number(p).toFixed(2)}</div></div>`)
        .join("");
    }
  } catch (e) {
    addr.value = "erro ao carregar";
  }

  $("#pasteBtn").onclick = async () => {
    try {
      const txt = await navigator.clipboard.readText();
      if (txt) hashInput.value = txt.trim();
    } catch {}
  };

  $("#validarBtn").onclick = async () => {
    alertBox.innerHTML = "";
    const h = (hashInput.value || "").trim();
    if (!(h.startsWith("0x") && h.length === 66)) {
      return showError("Hash inválido.");
    }
    try {
      const r = await fetch("/api/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hash: h })
      });
      const data = await r.json();
      if (!data.ok) return showError(data.error || data.message || "Falha ao validar.");
      showOk(data.message);
    } catch (e) {
      showError("Erro ao validar. Tente novamente.");
    }
  };

  // evita idle no Render (ping leve nos logs)
  setInterval(() => console.log("[keepalive] checkout open"), 60000);
})();
