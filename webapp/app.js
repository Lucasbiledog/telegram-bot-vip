(function () {
  const tg = window.Telegram?.WebApp;
  tg && tg.expand();

  // keep-alive no console a cada 25s
  setInterval(() => console.log("[webapp] alive", Date.now()), 25000);

  // lê uid da query
  const params = new URLSearchParams(location.search);
  const uid = Number(params.get("uid") || "0");

  // mostra carteira fixa embutida pelo servidor via env (renderizada no HTML via pequena injeção opcional)
  // como não temos templating aqui, vamos buscar /keepalive só para pegar a baseURL e usar wallet do .env injetada via data-attr no HTML
  fetch("/keepalive").catch(() => {});

  // você pode injetar a carteira por data-attr ou simplesmente fixar abaixo
  const WALLET = "0x40dDBD27F878d07808339F9965f013F1CBc2F812";
  document.getElementById("wallet").value = WALLET;

  // UI dos planos (só highlight visual)
  const plans = document.getElementById("plans");
  plans.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    plans.querySelectorAll("button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  });

  // Colar do clipboard
  document.getElementById("btnPaste").addEventListener("click", async () => {
    try {
      const t = await navigator.clipboard.readText();
      if (t && t.startsWith("0x")) document.getElementById("txhash").value = t.trim();
    } catch(e) {}
  });

  // Validar pagamento
  const status = document.getElementById("status");
  document.getElementById("btnValidate").addEventListener("click", async () => {
    const hash = document.getElementById("txhash").value.trim();
    if (!hash || !hash.startsWith("0x")) {
      status.textContent = "Informe um hash válido (0x...)";
      status.className = "status error";
      return;
    }
    status.textContent = "Validando pagamento…";
    status.className = "status info";
    try {
      const r = await fetch("/api/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uid, hash })
      });
      const data = await r.json();
      if (data.ok) {
        status.textContent = data.msg || "Pagamento aprovado. Convite enviado no bot.";
        status.className = "status ok";
      } else {
        status.textContent = data.msg || "Não foi possível validar. Tente novamente.";
        status.className = "status error";
      }
    } catch (e) {
      status.textContent = "Erro ao validar. Tente novamente.";
      status.className = "status error";
    }
  });
})();
