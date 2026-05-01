<!DOCTYPE html>
<html lang="fr" class="h-full">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TradingBot — Journal de Trading</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="preconnect" href="https://fonts.bunny.net">
<link href="https://fonts.bunny.net/css?family=geist:300,400,500,600&family=geist-mono:400" rel="stylesheet">
<script>tailwind.config={theme:{extend:{fontFamily:{sans:['Geist','sans-serif'],mono:['Geist Mono','monospace']}}}}</script>
<style>
*{box-sizing:border-box;}
body{background:#09090b;font-family:'Geist',sans-serif;}
::-webkit-scrollbar{width:3px;height:3px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.07);border-radius:99px;}

.nav-item{display:flex;align-items:center;gap:9px;padding:6px 10px;border-radius:7px;font-size:13px;color:#52525b;cursor:pointer;transition:all .15s;border:none;background:none;width:100%;text-align:left;}
.nav-item:hover{color:#d4d4d8;background:rgba(255,255,255,.04);}
.nav-item.active{color:#fafafa;background:rgba(255,255,255,.07);}
.nav-item svg{width:14px;height:14px;flex-shrink:0;opacity:.7;}
.nav-item.active svg{opacity:1;}
.nav-section{font-size:10px;font-weight:500;color:#3f3f46;letter-spacing:.07em;text-transform:uppercase;padding:10px 10px 3px;}
.topbar{backdrop-filter:blur(12px);background:rgba(9,9,11,.85);}

.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:500;white-space:nowrap;}
.badge-green{background:rgba(52,211,153,.1);color:#34d399;}
.badge-sky{background:rgba(56,189,248,.1);color:#38bdf8;}
.badge-amber{background:rgba(251,191,36,.1);color:#fbbf24;}
.badge-red{background:rgba(248,113,113,.1);color:#f87171;}
.badge-violet{background:rgba(167,139,250,.1);color:#a78bfa;}
.badge-zinc{background:rgba(255,255,255,.06);color:#71717a;}
.badge-teal{background:rgba(45,212,191,.1);color:#2dd4bf;}

.input{width:100%;padding:7px 11px;font-size:13px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:8px;color:#e4e4e7;font-family:'Geist',sans-serif;outline:none;transition:border-color .15s;}
.input:focus{border-color:rgba(56,189,248,.4);}
.input::placeholder{color:#3f3f46;}
select.input{cursor:pointer;}
textarea.input{resize:vertical;}

.btn-primary{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;font-size:12px;font-weight:500;background:#38bdf8;color:#082f49;border:none;border-radius:8px;cursor:pointer;transition:background .15s;font-family:'Geist',sans-serif;white-space:nowrap;}
.btn-primary:hover{background:#7dd3fc;}
.btn-ghost{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;font-size:12px;background:rgba(255,255,255,.05);color:#a1a1aa;border:1px solid rgba(255,255,255,.08);border-radius:8px;cursor:pointer;transition:all .15s;font-family:'Geist',sans-serif;white-space:nowrap;}
.btn-ghost:hover{background:rgba(255,255,255,.09);color:#e4e4e7;}
.btn-icon{width:28px;height:28px;display:flex;align-items:center;justify-content:center;color:#52525b;border:none;background:rgba(255,255,255,.04);border-radius:7px;cursor:pointer;transition:all .15s;flex-shrink:0;}
.btn-icon:hover{background:rgba(255,255,255,.08);color:#d4d4d8;}
.btn-danger{display:inline-flex;align-items:center;gap:5px;padding:5px 10px;font-size:11px;background:rgba(248,113,113,.08);color:#f87171;border:1px solid rgba(248,113,113,.18);border-radius:7px;cursor:pointer;font-family:'Geist',sans-serif;}

.card{background:#111113;border:1px solid rgba(255,255,255,.06);border-radius:12px;}
.tab{padding:5px 12px;font-size:12px;border-radius:7px;cursor:pointer;transition:all .15s;border:none;background:none;color:#52525b;font-family:'Geist',sans-serif;white-space:nowrap;}
.tab:hover{color:#a1a1aa;}
.tab.active{background:rgba(255,255,255,.07);color:#fafafa;}

/* ── Signal card ── */
.signal-card{background:#111113;border:1px solid rgba(255,255,255,.06);border-radius:12px;overflow:hidden;transition:all .18s;cursor:pointer;}
.signal-card:hover{border-color:rgba(255,255,255,.12);}
.signal-card.open-sig{border-color:rgba(56,189,248,.3);}
.signal-card.win{border-color:rgba(52,211,153,.25);}
.signal-card.loss{border-color:rgba(248,113,113,.2);}
.signal-accent{height:3px;width:100%;}

/* ── Telegram message preview ── */
.tg-phone{background:#1c2733;border-radius:12px;overflow:hidden;}
.tg-bar{background:#17212b;padding:8px 12px;display:flex;align-items:center;gap:8px;}
.tg-msg-area{padding:10px 12px;display:flex;flex-direction:column;gap:6px;}
.tg-bubble{background:#1e3040;border-radius:12px 12px 12px 2px;padding:10px 13px;font-size:12px;line-height:1.65;color:#e2e8f0;max-width:92%;}
.tg-inline-btns{display:flex;flex-direction:column;gap:4px;margin-top:2px;max-width:92%;}
.tg-btn{background:rgba(56,189,248,.1);border:1px solid rgba(56,189,248,.2);color:#64b5f6;border-radius:8px;padding:8px 12px;font-size:11px;text-align:center;cursor:pointer;transition:all .15s;font-family:'Geist',sans-serif;width:100%;}
.tg-btn:hover{background:rgba(56,189,248,.2);}
.tg-btn.green{background:rgba(52,211,153,.1);border-color:rgba(52,211,153,.25);color:#34d399;}
.tg-btn.green:hover{background:rgba(52,211,153,.2);}
.tg-btn.red{background:rgba(248,113,113,.1);border-color:rgba(248,113,113,.2);color:#f87171;}
.tg-btn.red:hover{background:rgba(248,113,113,.2);}
.tg-btn.amber{background:rgba(251,191,36,.1);border-color:rgba(251,191,36,.2);color:#fbbf24;}
.tg-btn.gray{background:rgba(255,255,255,.04);border-color:rgba(255,255,255,.08);color:#71717a;}
.tg-time{font-size:10px;color:#4a6478;text-align:right;margin-top:3px;}

/* ── Participation bar ── */
.part-bar{display:flex;height:6px;border-radius:99px;overflow:hidden;gap:1px;}
.part-seg{height:100%;transition:width .4s ease;}

/* ── Behavior ring ── */
.beh-ring{position:relative;display:flex;align-items:center;justify-content:center;}
.beh-label{position:absolute;text-align:center;line-height:1.2;}

/* ── Exit chart ── */
.exit-bar{display:flex;flex-direction:column;gap:4px;}
.exit-row{display:flex;align-items:center;gap:8px;font-size:11px;}
.exit-track{flex:1;height:6px;background:rgba(255,255,255,.06);border-radius:99px;overflow:hidden;}
.exit-fill{height:100%;border-radius:99px;}

/* ── Modal ── */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:60;display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .15s;}
.modal-overlay.open{opacity:1;pointer-events:auto;}
.modal{background:#111113;border:1px solid rgba(255,255,255,.1);border-radius:14px;transform:scale(.97);transition:transform .15s;max-height:92vh;display:flex;flex-direction:column;overflow:hidden;}
.modal-overlay.open .modal{transform:scale(1);}

/* ── Drawer ── */
.drawer-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:40;opacity:0;pointer-events:none;transition:opacity .2s;}
.drawer-overlay.open{opacity:1;pointer-events:auto;}
.drawer{position:fixed;top:0;right:0;bottom:0;background:#111113;border-left:1px solid rgba(255,255,255,.08);z-index:50;transform:translateX(100%);transition:transform .22s cubic-bezier(.4,0,.2,1);display:flex;flex-direction:column;overflow:hidden;}
.drawer.open{transform:translateX(0);}

/* ── PnL ── */
.pnl-pos{color:#34d399;font-weight:500;}
.pnl-neg{color:#f87171;font-weight:500;}
.pnl-zero{color:#71717a;}

/* ── Misc ── */
.pbar{height:3px;background:rgba(255,255,255,.06);border-radius:99px;overflow:hidden;}
.pbar-fill{height:100%;border-radius:99px;}
.stat-mini{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);border-radius:9px;padding:12px 14px;}
.member-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04);}
.member-row:last-child{border-bottom:none;}
.av{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:600;flex-shrink:0;}
.av-default{background:rgba(255,255,255,.07);color:#71717a;}
.av-green{background:rgba(52,211,153,.15);color:#34d399;}
.av-sky{background:rgba(56,189,248,.15);color:#38bdf8;}
.av-amber{background:rgba(251,191,36,.15);color:#fbbf24;}
.av-violet{background:rgba(167,139,250,.15);color:#a78bfa;}
.av-teal{background:rgba(45,212,191,.15);color:#2dd4bf;}
.av-red{background:rgba(248,113,113,.15);color:#f87171;}
.ia-chip{display:inline-flex;align-items:center;gap:4px;font-size:10px;padding:2px 7px;border-radius:99px;background:rgba(45,212,191,.12);color:#2dd4bf;font-weight:500;}
.ia-card{background:rgba(45,212,191,.04);border:1px solid rgba(45,212,191,.15);border-radius:12px;padding:16px;}
.slabel{font-size:10px;font-weight:500;color:#3f3f46;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px;}
.upload-zone{border:1px dashed rgba(255,255,255,.12);border-radius:9px;padding:16px;text-align:center;cursor:pointer;transition:all .15s;}
.upload-zone:hover{border-color:rgba(56,189,248,.35);background:rgba(56,189,248,.04);}
.toggle{width:30px;height:16px;background:rgba(255,255,255,.1);border-radius:99px;position:relative;cursor:pointer;transition:background .2s;flex-shrink:0;border:none;padding:0;}
.toggle.on{background:#38bdf8;}
.toggle::after{content:'';position:absolute;width:11px;height:11px;background:white;border-radius:50%;top:2.5px;left:2.5px;transition:transform .2s;}
.toggle.on::after{transform:translateX(14px);}
.step-dot{width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,.15);}
.step-dot.active{background:#38bdf8;}
.step-dot.done{background:#34d399;}
.dir-long{background:rgba(52,211,153,.12);color:#34d399;border:1px solid rgba(52,211,153,.2);padding:3px 10px;border-radius:6px;font-size:11px;font-weight:500;}
.dir-short{background:rgba(248,113,113,.12);color:#f87171;border:1px solid rgba(248,113,113,.2);padding:3px 10px;border-radius:6px;font-size:11px;font-weight:500;}
.live-dot{width:7px;height:7px;border-radius:50%;background:#38bdf8;flex-shrink:0;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.pulse{animation:pulse 2s ease infinite;}
@keyframes fadein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.fadein{animation:fadein .2s ease;}

/* ── Chart bar ── */
.chart-bar{background:rgba(56,189,248,.15);border-radius:3px 3px 0 0;transition:all .3s;cursor:pointer;}
.chart-bar.win-bar{background:rgba(52,211,153,.2);}
.chart-bar.loss-bar{background:rgba(248,113,113,.2);}

/* ── Engagement segment display ── */
.eng-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:8px;}
.eng-cell{padding:7px 8px;border-radius:7px;text-align:center;}

/* ── Behavior tag ── */
.beh-tag{display:inline-flex;align-items:center;gap:5px;padding:4px 9px;border-radius:7px;font-size:11px;font-weight:500;}
.beh-tag.disciplined{background:rgba(52,211,153,.1);color:#34d399;border:1px solid rgba(52,211,153,.2);}
.beh-tag.early-exit{background:rgba(251,191,36,.1);color:#fbbf24;border:1px solid rgba(251,191,36,.2);}
.beh-tag.sl-skip{background:rgba(248,113,113,.1);color:#f87171;border:1px solid rgba(248,113,113,.2);}
.beh-tag.passive{background:rgba(255,255,255,.05);color:#71717a;border:1px solid rgba(255,255,255,.08);}
</style>
</head>
<body class="h-screen overflow-hidden text-zinc-200">
<div class="flex h-full">

<!-- ─── SIDEBAR ─────────────────────────────────────────────────────── -->
<aside style="width:208px;flex-shrink:0;background:#0d0d0f;border-right:1px solid rgba(255,255,255,.05);" class="flex flex-col h-full">
  <div class="px-4 py-4" style="border-bottom:1px solid rgba(255,255,255,.05);">
    <div class="flex items-center gap-3">
      <div style="width:30px;height:30px;background:#0ea5e9;border-radius:8px;" class="flex items-center justify-center flex-shrink-0">
        <svg width="15" height="15" fill="white" viewBox="0 0 24 24"><path d="M12 0C5.37 0 0 5.37 0 12s5.37 12 12 12 12-5.37 12-12S18.63 0 12 0zm5.94 8.19l-2.02 9.52c-.14.66-.54.82-1.09.51l-3-2.21-1.45 1.39c-.16.16-.3.3-.61.3l.22-3.1 5.6-5.06c.24-.22-.06-.34-.38-.12L7.03 14.5 4.06 13.6c-.65-.2-.66-.65.14-.96l11.65-4.5c.54-.2 1.01.13.09 2.05z"/></svg>
      </div>
      <div><p class="text-sm font-medium text-white leading-none">TradingBot</p><p class="text-xs mt-0.5" style="color:#3f3f46;">3 247 membres</p></div>
    </div>
  </div>
  <nav class="flex-1 px-2 py-3 overflow-y-auto flex flex-col gap-0.5">
    <div class="nav-section">Vue d'ensemble</div>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>Dashboard</button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M3 3v18h18"/><path d="m7 16 4-4 4 4 4-6"/></svg>Statistiques</button>
    <div class="nav-section" style="margin-top:6px;">Membres</div>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>Utilisateurs<span class="badge badge-red ml-auto" style="font-size:10px;">12</span></button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>Catégories</button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/></svg>Formulaires</button>
    <div class="nav-section" style="margin-top:6px;">Messagerie</div>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="m22 2-7 20-4-9-9-4 20-7z"/></svg>Messages ciblés</button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>Chat direct<span class="badge badge-sky ml-auto" style="font-size:10px;">3</span></button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.22 4.22l2.12 2.12M17.66 17.66l2.12 2.12M2 12h3M19 12h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12"/></svg>Agent IA<span class="ml-auto flex items-center gap-1.5" style="font-size:10px;color:#34d399;"><span class="pulse" style="width:5px;height:5px;border-radius:50%;background:#34d399;display:block;"></span>live</span></button>
    <div class="nav-section" style="margin-top:6px;">Trading</div>
    <button class="nav-item active"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>Journal</button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>Témoignages</button>
    <div class="nav-section" style="margin-top:6px;">Gestion</div>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/></svg>Abonnements</button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>Rendez-vous</button>
    <button class="nav-item"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>Liens & Paiements</button>
  </nav>
  <div class="px-4 py-3" style="border-top:1px solid rgba(255,255,255,.05);">
    <div class="flex items-center gap-2.5">
      <div style="width:27px;height:27px;border-radius:50%;background:linear-gradient(135deg,#0ea5e9,#6366f1);flex-shrink:0;"></div>
      <div class="flex-1 min-w-0"><p class="text-xs font-medium text-zinc-300 truncate">Admin</p><p class="text-[10px]" style="color:#3f3f46;">admin@tradingbot.io</p></div>
    </div>
  </div>
</aside>

<!-- ─── MAIN ──────────────────────────────────────────────────────────── -->
<div class="flex-1 flex flex-col min-w-0 overflow-hidden">

  <header class="topbar flex-shrink-0 flex items-center justify-between px-6" style="height:52px;border-bottom:1px solid rgba(255,255,255,.05);">
    <div class="flex items-center gap-3">
      <h1 class="text-sm font-medium text-white">Journal de trading</h1>
      <span style="color:#27272a;">·</span>
      <div class="flex items-center gap-0.5" style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:2px;">
        <button class="tab active" id="tab-journal"      onclick="switchView('journal',this)">Signaux</button>
        <button class="tab"         id="tab-members"     onclick="switchView('members',this)">Performances</button>
        <button class="tab"         id="tab-leaderboard" onclick="switchView('leaderboard',this)">Classement</button>
        <button class="tab"         id="tab-ia"          onclick="switchView('ia',this)">Bilan IA</button>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <select class="input" style="width:130px;font-size:11px;padding:5px 9px;">
        <option>Cette semaine</option><option>Ce mois</option><option>Tout</option>
      </select>
      <button class="btn-ghost" style="font-size:11px;">
        <svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/></svg>
        Export
      </button>
      <button class="btn-primary" onclick="openModal('modal-publish')">
        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" d="M12 4v16m8-8H4"/></svg>
        Publier un trade
      </button>
    </div>
  </header>

  <main class="flex-1 overflow-y-auto p-5 flex flex-col gap-4">

  <!-- ══ VUE SIGNAUX ══════════════════════════════════════════════════ -->
  <div id="view-journal">

    <!-- Stats admin -->
    <div class="grid grid-cols-5 gap-3 mb-4">
      <div class="stat-mini"><p class="text-[10px] mb-2" style="color:#52525b;">Trades publiés</p><p class="text-xl font-light text-white tabular-nums">47</p><p class="text-[10px] mt-1" style="color:#52525b;">ce mois</p></div>
      <div class="stat-mini"><p class="text-[10px] mb-2" style="color:#52525b;">Win rate admin</p><p class="text-xl font-light tabular-nums pnl-pos">71%</p><div class="pbar mt-2"><div class="pbar-fill" style="width:71%;background:#34d399;"></div></div></div>
      <div class="stat-mini"><p class="text-[10px] mb-2" style="color:#52525b;">Taux engagement</p><p class="text-xl font-light tabular-nums" style="color:#38bdf8;">34%</p><p class="text-[10px] mt-1" style="color:#52525b;">répondent "Je suis dedans"</p></div>
      <div class="stat-mini"><p class="text-[10px] mb-2" style="color:#52525b;">Formulaires collectés</p><p class="text-xl font-light text-white tabular-nums">612</p><p class="text-[10px] mt-1" style="color:#52525b;">journaux membres</p></div>
      <div class="stat-mini"><p class="text-[10px] mb-2" style="color:#52525b;">Trades ouverts</p><p class="text-xl font-light tabular-nums" style="color:#38bdf8;">2</p><span class="flex items-center gap-1.5 mt-1" style="font-size:10px;color:#38bdf8;"><span class="live-dot pulse" style="width:5px;height:5px;animation:pulse 2s infinite;"></span>En cours</span></div>
    </div>

    <!-- Mini chart -->
    <div class="card p-5 mb-4">
      <div class="flex items-center justify-between mb-4">
        <p class="text-sm font-medium text-white">Performance hebdomadaire</p>
        <div class="flex items-center gap-4 text-[11px]" style="color:#71717a;">
          <span class="flex items-center gap-1.5"><span style="width:8px;height:8px;border-radius:2px;background:rgba(52,211,153,.3);display:inline-block;"></span>Win</span>
          <span class="flex items-center gap-1.5"><span style="width:8px;height:8px;border-radius:2px;background:rgba(248,113,113,.3);display:inline-block;"></span>Loss</span>
        </div>
      </div>
      <div class="flex items-end gap-2" style="height:70px;">
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar win-bar w-full" style="height:65%;"></div><span class="text-[9px]" style="color:#52525b;">Lun</span></div>
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar win-bar w-full" style="height:80%;"></div><span class="text-[9px]" style="color:#52525b;">Mar</span></div>
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar loss-bar w-full" style="height:40%;"></div><span class="text-[9px]" style="color:#52525b;">Mer</span></div>
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar win-bar w-full" style="height:90%;"></div><span class="text-[9px]" style="color:#52525b;">Jeu</span></div>
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar win-bar w-full" style="height:55%;"></div><span class="text-[9px]" style="color:#52525b;">Ven</span></div>
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar w-full" style="height:15%;background:rgba(255,255,255,.06);"></div><span class="text-[9px]" style="color:#3f3f46;">Sam</span></div>
        <div class="flex flex-col items-center gap-1 flex-1"><div class="chart-bar w-full" style="height:15%;background:rgba(255,255,255,.06);"></div><span class="text-[9px]" style="color:#3f3f46;">Dim</span></div>
      </div>
    </div>

    <!-- Grille signaux -->
    <div class="grid grid-cols-2 gap-4">

      <!-- ─── SIGNAL 1 : Ouvert ────────────────────────────── -->
      <div class="signal-card open-sig fadein" onclick="openSignalDrawer('sig-1')">
        <div class="signal-accent" style="background:#38bdf8;"></div>
        <div class="p-4">
          <!-- Header -->
          <div class="flex items-start justify-between mb-3">
            <div>
              <div class="flex items-center gap-2 mb-1">
                <span class="text-base font-medium text-white" style="font-family:'Geist Mono',monospace;">EUR/USD</span>
                <span class="dir-long">LONG</span>
                <span class="flex items-center gap-1 text-[10px]" style="color:#38bdf8;"><span class="live-dot pulse" style="width:5px;height:5px;animation:pulse 2s infinite;"></span>En cours</span>
              </div>
              <p class="text-[11px]" style="color:#52525b;">H4 · Aujourd'hui 09:14 · Clients actifs</p>
            </div>
            <button class="btn-icon" onclick="event.stopPropagation();openModal('modal-close-trade')" title="Clôturer" style="color:#fbbf24;background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.2);">
              <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
            </button>
          </div>

          <!-- Niveaux -->
          <div class="grid grid-cols-4 gap-2 mb-3">
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Entrée</p><p class="text-xs tabular-nums text-white" style="font-family:'Geist Mono',monospace;">1.0842</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#34d399;">TP1</p><p class="text-xs tabular-nums" style="color:#34d399;font-family:'Geist Mono',monospace;">1.0920</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#34d399;opacity:.7;">TP2</p><p class="text-xs tabular-nums" style="color:#34d399;opacity:.7;font-family:'Geist Mono',monospace;">1.0960</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#f87171;">SL</p><p class="text-xs tabular-nums" style="color:#f87171;font-family:'Geist Mono',monospace;">1.0800</p></div>
          </div>

          <!-- Participation en temps réel -->
          <div style="background:rgba(255,255,255,.025);border-radius:8px;padding:10px 12px;">
            <div class="flex items-center justify-between mb-2">
              <span class="text-[11px] font-medium text-zinc-300">Participation temps réel</span>
              <span class="text-[10px]" style="color:#52525b;">847 destinataires</span>
            </div>
            <!-- Barre tricolore -->
            <div class="part-bar mb-2">
              <div class="part-seg" style="width:28%;background:#34d399;border-radius:99px 0 0 99px;"></div>
              <div class="part-seg" style="width:15%;background:#f87171;"></div>
              <div class="part-seg" style="width:57%;background:rgba(255,255,255,.06);border-radius:0 99px 99px 0;"></div>
            </div>
            <div class="eng-grid">
              <div class="eng-cell" style="background:rgba(52,211,153,.07);border:1px solid rgba(52,211,153,.15);">
                <p class="text-sm font-light tabular-nums" style="color:#34d399;">237</p>
                <p class="text-[9px] mt-0.5" style="color:#34d399;opacity:.8;">✅ Suis le trade</p>
              </div>
              <div class="eng-cell" style="background:rgba(248,113,113,.07);border:1px solid rgba(248,113,113,.15);">
                <p class="text-sm font-light tabular-nums" style="color:#f87171;">127</p>
                <p class="text-[9px] mt-0.5" style="color:#f87171;opacity:.8;">❌ Ne prend pas</p>
              </div>
              <div class="eng-cell" style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);">
                <p class="text-sm font-light tabular-nums" style="color:#71717a;">483</p>
                <p class="text-[9px] mt-0.5" style="color:#52525b;">⏳ Pas répondu</p>
              </div>
            </div>
            <p class="text-[10px] mt-2" style="color:#52525b;">En attente de clôture pour déclencher le formulaire de résultats</p>
          </div>
        </div>
      </div>

      <!-- ─── SIGNAL 2 : Win clôturé ──────────────────────── -->
      <div class="signal-card win fadein" onclick="openSignalDrawer('sig-2')">
        <div class="signal-accent" style="background:#34d399;"></div>
        <div class="p-4">
          <div class="flex items-start justify-between mb-3">
            <div>
              <div class="flex items-center gap-2 mb-1">
                <span class="text-base font-medium text-white" style="font-family:'Geist Mono',monospace;">XAU/USD</span>
                <span class="dir-long">LONG</span>
                <span class="badge badge-green" style="font-size:10px;">TP1 ✓</span>
              </div>
              <p class="text-[11px]" style="color:#52525b;">H1 · Hier 14:30 → 18:22 · Clients actifs + Premium</p>
            </div>
            <div class="text-right">
              <p class="text-lg font-light tabular-nums pnl-pos">+4.2%</p>
              <p class="text-[10px]" style="color:#52525b;">+42 pips</p>
            </div>
          </div>
          <div class="grid grid-cols-4 gap-2 mb-3">
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Entrée</p><p class="text-xs tabular-nums text-white" style="font-family:'Geist Mono',monospace;">2 320</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#34d399;">TP1</p><p class="text-xs tabular-nums" style="color:#34d399;font-family:'Geist Mono',monospace;">2 387</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Clôture</p><p class="text-xs tabular-nums pnl-pos" style="font-family:'Geist Mono',monospace;">2 387</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">R:R</p><p class="text-xs tabular-nums text-white">1:2.6</p></div>
          </div>

          <!-- Stats participation + comportement -->
          <div style="background:rgba(52,211,153,.04);border:1px solid rgba(52,211,153,.1);border-radius:8px;padding:10px 12px;">
            <div class="flex items-center justify-between mb-2">
              <span class="text-[11px] font-medium text-zinc-300">Résultats collectés</span>
              <span class="text-[11px]" style="color:#34d399;">189 / 237 réponses</span>
            </div>
            <!-- Barre engagement initial -->
            <div class="flex items-center gap-2 mb-2">
              <span class="text-[10px]" style="color:#52525b;min-width:60px;">Participation</span>
              <div class="part-bar flex-1" style="height:5px;">
                <div class="part-seg" style="width:64%;background:#34d399;border-radius:99px 0 0 99px;"></div>
                <div class="part-seg" style="width:17%;background:#f87171;"></div>
                <div class="part-seg" style="width:19%;background:rgba(255,255,255,.06);border-radius:0 99px 99px 0;"></div>
              </div>
              <span class="text-[10px]" style="color:#52525b;">237·127·133</span>
            </div>
            <!-- Comportement à la clôture -->
            <div class="flex items-center gap-2 mb-2">
              <span class="text-[10px]" style="color:#52525b;min-width:60px;">Comportement</span>
              <div class="flex gap-1.5 flex-wrap">
                <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(52,211,153,.1);color:#34d399;">142 TP ✓</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(251,191,36,.1);color:#fbbf24;">31 sortie anticipée</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(248,113,113,.1);color:#f87171;">16 SL</span>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <div class="flex gap-3 text-[10px]">
                <span style="color:#34d399;">75% win rate</span>
                <span style="color:#38bdf8;">+3.8% moy.</span>
              </div>
              <button class="text-[10px]" style="color:#38bdf8;background:none;border:none;cursor:pointer;" onclick="event.stopPropagation();openSignalDrawer('sig-2')">Détail →</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ─── SIGNAL 3 : Loss ──────────────────────────────── -->
      <div class="signal-card loss fadein" onclick="openSignalDrawer('sig-3')">
        <div class="signal-accent" style="background:#f87171;"></div>
        <div class="p-4">
          <div class="flex items-start justify-between mb-3">
            <div>
              <div class="flex items-center gap-2 mb-1">
                <span class="text-base font-medium text-white" style="font-family:'Geist Mono',monospace;">GBP/JPY</span>
                <span class="dir-short">SHORT</span>
                <span class="badge badge-red" style="font-size:10px;">SL ✗</span>
              </div>
              <p class="text-[11px]" style="color:#52525b;">H4 · Il y a 2j · Tous</p>
            </div>
            <div class="text-right">
              <p class="text-lg font-light tabular-nums pnl-neg">-1.8%</p>
              <p class="text-[10px]" style="color:#52525b;">-18 pips</p>
            </div>
          </div>
          <div class="grid grid-cols-4 gap-2 mb-3">
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Entrée</p><p class="text-xs tabular-nums text-white" style="font-family:'Geist Mono',monospace;">192.40</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#34d399;">TP1</p><p class="text-xs tabular-nums" style="color:#34d399;opacity:.5;font-family:'Geist Mono',monospace;">191.00</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Clôture</p><p class="text-xs tabular-nums pnl-neg" style="font-family:'Geist Mono',monospace;">193.80</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#f87171;">SL</p><p class="text-xs tabular-nums" style="color:#f87171;font-family:'Geist Mono',monospace;">193.80</p></div>
          </div>
          <div style="background:rgba(248,113,113,.04);border:1px solid rgba(248,113,113,.1);border-radius:8px;padding:10px 12px;">
            <div class="flex items-center justify-between mb-2">
              <span class="text-[11px] font-medium text-zinc-300">Résultats collectés</span>
              <span class="text-[11px]" style="color:#f87171;">98 / 3 247 réponses</span>
            </div>
            <!-- Engagement initial faible car envoyé à tous -->
            <div class="flex items-center gap-2 mb-2">
              <span class="text-[10px]" style="color:#52525b;min-width:60px;">Participation</span>
              <div class="part-bar flex-1" style="height:5px;">
                <div class="part-seg" style="width:3%;background:#34d399;border-radius:99px 0 0 99px;"></div>
                <div class="part-seg" style="width:2%;background:#f87171;"></div>
                <div class="part-seg" style="width:95%;background:rgba(255,255,255,.06);border-radius:0 99px 99px 0;"></div>
              </div>
            </div>
            <div class="flex gap-1.5 flex-wrap mb-1">
              <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(52,211,153,.1);color:#34d399;">12 ont respecté le SL ✓</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(248,113,113,.1);color:#f87171;">78 ont gardé / aggravé</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(255,255,255,.05);color:#71717a;">8 n'étaient pas dedans</span>
            </div>
            <p class="text-[10px]" style="color:#fbbf24;">⚠️ 80% des membres n'ont pas respecté le SL sur ce trade</p>
          </div>
        </div>
      </div>

      <!-- ─── SIGNAL 4 : Win partiel ───────────────────────── -->
      <div class="signal-card win fadein" onclick="openSignalDrawer('sig-4')">
        <div class="signal-accent" style="background:#fbbf24;"></div>
        <div class="p-4">
          <div class="flex items-start justify-between mb-3">
            <div>
              <div class="flex items-center gap-2 mb-1">
                <span class="text-base font-medium text-white" style="font-family:'Geist Mono',monospace;">BTC/USD</span>
                <span class="dir-short">SHORT</span>
                <span class="badge badge-amber" style="font-size:10px;">Partiel</span>
              </div>
              <p class="text-[11px]" style="color:#52525b;">D1 · Il y a 3j · Premium</p>
            </div>
            <div class="text-right">
              <p class="text-lg font-light tabular-nums pnl-pos">+2.1%</p>
              <p class="text-[10px]" style="color:#52525b;">TP1 atteint</p>
            </div>
          </div>
          <div class="grid grid-cols-4 gap-2 mb-3">
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Entrée</p><p class="text-xs tabular-nums text-white" style="font-family:'Geist Mono',monospace;">67 200</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#34d399;">TP1</p><p class="text-xs tabular-nums" style="color:#34d399;font-family:'Geist Mono',monospace;">65 800</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#52525b;">Clôture</p><p class="text-xs tabular-nums pnl-pos" style="font-family:'Geist Mono',monospace;">65 800</p></div>
            <div class="stat-mini text-center" style="padding:7px 5px;"><p class="text-[9px] mb-1" style="color:#f87171;">SL</p><p class="text-xs tabular-nums" style="color:#f87171;font-family:'Geist Mono',monospace;">68 500</p></div>
          </div>
          <div style="background:rgba(52,211,153,.04);border:1px solid rgba(52,211,153,.1);border-radius:8px;padding:10px 12px;">
            <div class="flex items-center justify-between mb-2">
              <span class="text-[11px] font-medium text-zinc-300">Résultats collectés</span>
              <span class="text-[11px]" style="color:#34d399;">87 / 312 (Premium)</span>
            </div>
            <div class="flex gap-1.5 flex-wrap mb-1">
              <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(52,211,153,.1);color:#34d399;">61 TP ✓</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(251,191,36,.1);color:#fbbf24;">19 sortie anticipée</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" style="background:rgba(248,113,113,.1);color:#f87171;">7 SL</span>
            </div>
            <div class="flex gap-3 text-[10px]"><span style="color:#34d399;">70% win rate</span><span style="color:#38bdf8;">+1.9% moy. réelle</span></div>
          </div>
        </div>
      </div>

    </div><!-- end grid -->
  </div>

  <!-- ══ VUE PERFORMANCES ═════════════════════════════════════════════ -->
  <div id="view-members" style="display:none;">
    <div class="flex items-center gap-3 mb-4">
      <div class="relative" style="width:240px;">
        <svg width="12" height="12" fill="none" stroke="#3f3f46" viewBox="0 0 24 24" stroke-width="2" style="position:absolute;left:9px;top:50%;transform:translateY(-50%);"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input class="input" type="text" placeholder="Chercher un membre..." style="padding-left:28px;font-size:12px;">
      </div>
      <select class="input" style="width:160px;font-size:12px;"><option>Trier : Win rate ↓</option><option>Trier : Discipline ↓</option><option>Trier : Engagement ↓</option></select>
    </div>
    <div class="card overflow-hidden">
      <div class="flex items-center gap-4 px-4 py-3" style="background:rgba(255,255,255,.02);border-bottom:1px solid rgba(255,255,255,.05);">
        <span class="text-[10px] font-medium flex-1" style="color:#3f3f46;">Membre</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:60px;">Trades</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:70px;">Engagement</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:70px;">Win rate</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:80px;">Perf. totale</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:110px;">Comportement</span>
        <span style="width:28px;"></span>
      </div>

      <div class="member-row px-4" style="cursor:pointer;" onclick="openMemberDrawer('mr')">
        <div class="flex items-center gap-2 flex-1"><div class="av av-green">MR</div><div><p class="text-xs font-medium text-zinc-200">Marc Renaud</p><p class="text-[10px]" style="color:#52525b;">@marc_renaud</p></div></div>
        <span class="text-xs tabular-nums text-zinc-300" style="width:60px;">12</span>
        <div style="width:70px;"><p class="text-xs tabular-nums" style="color:#38bdf8;">83%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:83%;background:#38bdf8;"></div></div></div>
        <div style="width:70px;"><p class="text-xs tabular-nums pnl-pos">71%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:71%;background:#34d399;"></div></div></div>
        <span class="text-xs tabular-nums pnl-pos" style="width:80px;">+33.6%</span>
        <div style="width:110px;"><span class="beh-tag disciplined" style="font-size:9px;">Discipliné ✓</span></div>
        <button class="btn-icon" style="width:24px;height:24px;" onclick="event.stopPropagation();openMemberDrawer('mr')"><svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
      </div>

      <div class="member-row px-4" style="cursor:pointer;" onclick="openMemberDrawer('lb')">
        <div class="flex items-center gap-2 flex-1"><div class="av av-violet">LB</div><div><p class="text-xs font-medium text-zinc-200">Lucie Bernard</p><p class="text-[10px]" style="color:#52525b;">@lucie_b</p></div></div>
        <span class="text-xs tabular-nums text-zinc-300" style="width:60px;">9</span>
        <div style="width:70px;"><p class="text-xs tabular-nums" style="color:#38bdf8;">89%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:89%;background:#38bdf8;"></div></div></div>
        <div style="width:70px;"><p class="text-xs tabular-nums pnl-pos">78%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:78%;background:#34d399;"></div></div></div>
        <span class="text-xs tabular-nums pnl-pos" style="width:80px;">+30.6%</span>
        <div style="width:110px;"><span class="beh-tag disciplined" style="font-size:9px;">Discipliné ✓</span></div>
        <button class="btn-icon" style="width:24px;height:24px;" onclick="event.stopPropagation();openMemberDrawer('lb')"><svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
      </div>

      <div class="member-row px-4" style="cursor:pointer;" onclick="openMemberDrawer('sa')">
        <div class="flex items-center gap-2 flex-1"><div class="av av-sky">SA</div><div><p class="text-xs font-medium text-zinc-200">Sophie Amar</p><p class="text-[10px]" style="color:#52525b;">@sophie_a</p></div></div>
        <span class="text-xs tabular-nums text-zinc-300" style="width:60px;">7</span>
        <div style="width:70px;"><p class="text-xs tabular-nums" style="color:#fbbf24;">57%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:57%;background:#fbbf24;"></div></div></div>
        <div style="width:70px;"><p class="text-xs tabular-nums pnl-pos">57%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:57%;background:#38bdf8;"></div></div></div>
        <span class="text-xs tabular-nums pnl-pos" style="width:80px;">+8.4%</span>
        <div style="width:110px;"><span class="beh-tag early-exit" style="font-size:9px;">Sortie tôt ⚡</span></div>
        <button class="btn-icon" style="width:24px;height:24px;" onclick="event.stopPropagation();openMemberDrawer('sa')"><svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
      </div>

      <div class="member-row px-4" style="cursor:pointer;opacity:.75;">
        <div class="flex items-center gap-2 flex-1"><div class="av av-amber">TK</div><div><p class="text-xs font-medium text-zinc-200">Thomas Klein</p><p class="text-[10px]" style="color:#52525b;">@t_klein</p></div></div>
        <span class="text-xs tabular-nums text-zinc-300" style="width:60px;">3</span>
        <div style="width:70px;"><p class="text-xs tabular-nums" style="color:#f87171;">33%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:33%;background:#f87171;"></div></div></div>
        <div style="width:70px;"><p class="text-xs tabular-nums pnl-neg">33%</p><div class="pbar mt-1"><div class="pbar-fill" style="width:33%;background:#f87171;"></div></div></div>
        <span class="text-xs tabular-nums pnl-neg" style="width:80px;">-1.8%</span>
        <div style="width:110px;"><span class="beh-tag sl-skip" style="font-size:9px;">Ignore le SL ⚠️</span></div>
        <button class="btn-icon" style="width:24px;height:24px;"><svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
      </div>

      <div class="flex items-center justify-between px-4 py-3" style="border-top:1px solid rgba(255,255,255,.04);background:rgba(255,255,255,.01);">
        <span class="text-xs" style="color:#52525b;">Affichage 1–4 sur 189</span>
        <div class="flex items-center gap-1.5">
          <button class="btn-icon" style="opacity:.4;" disabled><svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg></button>
          <span class="text-xs px-2" style="color:#71717a;">1 / 48</span>
          <button class="btn-icon"><svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
      </div>
    </div>
  </div>

  <!-- ══ VUE CLASSEMENT ══════════════════════════════════════════════ -->
  <div id="view-leaderboard" style="display:none;" class="flex flex-col gap-4">
    <div class="grid grid-cols-3 gap-3 mb-2">
      <div class="card p-5 text-center" style="margin-top:20px;"><div style="font-size:28px;margin-bottom:8px;">🥈</div><div class="av av-violet mx-auto mb-2" style="width:40px;height:40px;font-size:13px;">LB</div><p class="text-sm font-medium text-zinc-200">Lucie Bernard</p><p class="text-xl font-light mt-1 pnl-pos">+30.6%</p><p class="text-[10px] mt-1" style="color:#52525b;">9 trades · 78% win · 89% engag.</p></div>
      <div class="card p-5 text-center" style="border-color:rgba(251,191,36,.25);background:rgba(251,191,36,.03);"><div style="font-size:32px;margin-bottom:8px;">🥇</div><div class="av av-green mx-auto mb-2" style="width:44px;height:44px;font-size:14px;">MR</div><p class="text-sm font-medium text-white">Marc Renaud</p><p class="text-2xl font-light mt-1 pnl-pos">+33.6%</p><p class="text-[10px] mt-1" style="color:#52525b;">12 trades · 71% win · 83% engag.</p></div>
      <div class="card p-5 text-center" style="margin-top:32px;"><div style="font-size:26px;margin-bottom:8px;">🥉</div><div class="av av-sky mx-auto mb-2" style="width:36px;height:36px;font-size:12px;">SA</div><p class="text-sm font-medium text-zinc-200">Sophie Amar</p><p class="text-xl font-light mt-1 pnl-pos">+8.4%</p><p class="text-[10px] mt-1" style="color:#52525b;">7 trades · 57% win · 57% engag.</p></div>
    </div>
    <div class="card overflow-hidden">
      <div class="flex items-center gap-4 px-4 py-3" style="background:rgba(255,255,255,.02);border-bottom:1px solid rgba(255,255,255,.05);">
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:30px;">#</span>
        <span class="text-[10px] font-medium flex-1" style="color:#3f3f46;">Membre</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:70px;">Trades</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:70px;">Engagement</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:70px;">Win rate</span>
        <span class="text-[10px] font-medium" style="color:#3f3f46;width:90px;">Perf. totale</span>
      </div>
      <div class="member-row px-4"><span style="font-size:13px;font-weight:600;color:#fbbf24;min-width:30px;text-align:center;">4</span><div class="flex items-center gap-2 flex-1"><div class="av av-teal" style="font-size:10px;">NM</div><p class="text-xs text-zinc-200">Nicolas Morel</p></div><span class="text-xs tabular-nums text-zinc-400" style="width:70px;">5</span><span class="text-xs tabular-nums" style="color:#38bdf8;width:70px;">74%</span><span class="text-xs tabular-nums" style="color:#fbbf24;width:70px;">60%</span><span class="text-xs tabular-nums pnl-pos" style="width:90px;">+6.2%</span></div>
      <div class="member-row px-4"><span style="font-size:13px;font-weight:600;color:#71717a;min-width:30px;text-align:center;">5</span><div class="flex items-center gap-2 flex-1"><div class="av av-amber" style="font-size:10px;">TK</div><p class="text-xs text-zinc-200">Thomas Klein</p></div><span class="text-xs tabular-nums text-zinc-400" style="width:70px;">3</span><span class="text-xs tabular-nums" style="color:#f87171;width:70px;">33%</span><span class="text-xs tabular-nums" style="color:#f87171;width:70px;">33%</span><span class="text-xs tabular-nums pnl-neg" style="width:90px;">-1.8%</span></div>
      <p class="text-xs text-center py-3" style="color:#3f3f46;">+ 183 autres · minimum 3 trades pour figurer</p>
    </div>
  </div>

  <!-- ══ VUE BILAN IA ═════════════════════════════════════════════════ -->
  <div id="view-ia" style="display:none;" class="flex flex-col gap-4">
    <div class="grid grid-cols-2 gap-4">
      <div class="flex flex-col gap-4">
        <div class="ia-card">
          <div class="flex items-center gap-2 mb-4"><span class="ia-chip">Agent IA</span><p class="text-sm font-medium text-white">Bilan hebdomadaire personnalisé</p></div>
          <div class="flex flex-col gap-3 mb-4">
            <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Semaine</p><select class="input" style="font-size:12px;background:rgba(255,255,255,.04);"><option>Semaine du 14 au 20 avril 2026</option><option>Semaine du 7 au 13 avril</option></select></div>
            <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Envoyer à</p><select class="input" style="font-size:12px;background:rgba(255,255,255,.04);"><option>Membres ayant journalisé ≥1 trade (189)</option><option>Clients actifs</option><option>Tous</option></select></div>
            <div><p class="text-[10px] mb-2" style="color:#52525b;">Inclure dans le bilan</p>
              <div class="flex flex-col gap-1.5">
                <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer"><input type="checkbox" checked style="accent-color:#38bdf8;"> Résumé perf. de la semaine</label>
                <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer"><input type="checkbox" checked style="accent-color:#38bdf8;"> Analyse comportement (SL / TP / sortie anticipée)</label>
                <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer"><input type="checkbox" checked style="accent-color:#38bdf8;"> Recommandations personnalisées</label>
                <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer"><input type="checkbox" style="accent-color:#38bdf8;"> Comparaison avec le résultat théorique du signal</label>
              </div>
            </div>
          </div>
          <button class="btn-primary w-full justify-center" onclick="generateBilan()">
            <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.22 4.22l2.12 2.12M17.66 17.66l2.12 2.12M2 12h3M19 12h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12"/></svg>
            Générer les bilans
          </button>
        </div>
        <div class="card p-4">
          <div class="flex items-center justify-between mb-3"><p class="text-xs font-medium text-zinc-300">Aperçu — Marc Renaud</p><span class="ia-chip" id="ia-status">En attente</span></div>
          <div id="ia-preview-box" style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:12px;font-size:12px;line-height:1.7;color:#52525b;min-height:130px;font-style:italic;">Cliquer sur "Générer" pour voir un exemple...</div>
          <div class="flex gap-2 mt-3">
            <button class="btn-ghost flex-1 justify-center" style="font-size:11px;">Régénérer</button>
            <button class="btn-primary flex-1 justify-center" style="font-size:11px;" onclick="openModal('modal-send-bilan')">Envoyer à tous →</button>
          </div>
        </div>
      </div>
      <div class="flex flex-col gap-3">
        <div class="card p-4">
          <p class="text-sm font-medium text-white mb-4">Bilans envoyés</p>
          <div class="flex flex-col gap-2">
            <div style="padding:10px 12px;background:rgba(255,255,255,.025);border-radius:8px;border:1px solid rgba(255,255,255,.05);"><div class="flex justify-between mb-1"><p class="text-xs font-medium text-zinc-200">Semaine #16</p><span class="badge badge-green" style="font-size:10px;">Envoyé</span></div><p class="text-[11px]" style="color:#52525b;">7–13 avril · 124 bilans · 92% ouverts</p></div>
            <div style="padding:10px 12px;background:rgba(255,255,255,.025);border-radius:8px;border:1px solid rgba(255,255,255,.05);"><div class="flex justify-between mb-1"><p class="text-xs font-medium text-zinc-200">Semaine #15</p><span class="badge badge-green" style="font-size:10px;">Envoyé</span></div><p class="text-[11px]" style="color:#52525b;">31 mars–6 avril · 118 bilans · 88% ouverts</p></div>
          </div>
        </div>
        <div class="card p-4">
          <p class="text-xs font-medium text-zinc-300 mb-3">Impact mesurable</p>
          <div class="flex flex-col gap-2.5">
            <div class="flex justify-between"><span class="text-[11px]" style="color:#52525b;">Win rate semaine suivante (après bilan)</span><span class="text-[11px] font-medium pnl-pos">+11%</span></div>
            <div class="flex justify-between"><span class="text-[11px]" style="color:#52525b;">Membres ayant amélioré leur respect du SL</span><span class="text-[11px] font-medium" style="color:#38bdf8;">+34%</span></div>
            <div class="flex justify-between"><span class="text-[11px]" style="color:#52525b;">Taux d'ouverture moyen des bilans</span><span class="text-[11px] font-medium" style="color:#38bdf8;">88%</span></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  </main>
</div>
</div>

<!-- ══════════════════════════════════════════════════════════════════════ -->
<!-- MODAL : PUBLIER UN TRADE                                               -->
<!-- ══════════════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="modal-publish">
  <div class="modal" style="width:620px;">
    <div class="flex items-center justify-between px-6 py-4" style="border-bottom:1px solid rgba(255,255,255,.06);">
      <div><p class="text-sm font-medium text-white">Publier un signal</p><p class="text-[11px] mt-0.5" style="color:#52525b;">Signal → broadcast + boutons participation</p></div>
      <button class="btn-icon" onclick="closeModal('modal-publish')"><svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" d="M18 6 6 18M6 6l12 12"/></svg></button>
    </div>
    <!-- Steps -->
    <div class="flex items-center gap-2 px-6 py-3" style="border-bottom:1px solid rgba(255,255,255,.05);">
      <div class="flex items-center gap-2"><div class="step-dot active" id="sdot-1"></div><span class="text-xs" style="color:#38bdf8;" id="slbl-1">1 · Signal</span></div>
      <div style="flex:1;height:1px;background:rgba(255,255,255,.06);"></div>
      <div class="flex items-center gap-2"><div class="step-dot" id="sdot-2"></div><span class="text-xs" style="color:#3f3f46;" id="slbl-2">2 · Diffusion</span></div>
      <div style="flex:1;height:1px;background:rgba(255,255,255,.06);"></div>
      <div class="flex items-center gap-2"><div class="step-dot" id="sdot-3"></div><span class="text-xs" style="color:#3f3f46;" id="slbl-3">3 · Aperçu</span></div>
    </div>

    <!-- Step 1 -->
    <div id="pub-s1" class="px-6 py-5 overflow-y-auto" style="max-height:55vh;">
      <div class="grid grid-cols-2 gap-3 mb-3">
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Paire</p><select class="input" id="sig-pair" onchange="updatePreview()"><option>EUR/USD</option><option>GBP/USD</option><option>XAU/USD</option><option>BTC/USD</option><option>GBP/JPY</option><option>NAS100</option></select></div>
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Direction</p>
          <div class="flex gap-2">
            <button id="dir-long" onclick="setDir('long')" style="flex:1;padding:7px;border-radius:8px;cursor:pointer;font-size:12px;font-family:'Geist',sans-serif;border:1px solid rgba(52,211,153,.3);background:rgba(52,211,153,.1);color:#34d399;font-weight:500;">📈 LONG</button>
            <button id="dir-short" onclick="setDir('short')" style="flex:1;padding:7px;border-radius:8px;cursor:pointer;font-size:12px;font-family:'Geist',sans-serif;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.04);color:#71717a;">📉 SHORT</button>
          </div>
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3 mb-3">
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Prix d'entrée</p><input class="input" type="text" id="sig-entry" placeholder="ex: 1.0842" style="font-family:'Geist Mono',monospace;" oninput="updatePreview();calcRR()"></div>
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Timeframe</p><select class="input"><option>M15</option><option>M30</option><option>H1</option><option selected>H4</option><option>D1</option></select></div>
      </div>
      <div class="grid grid-cols-3 gap-3 mb-3">
        <div><p class="text-[10px] mb-1.5" style="color:#34d399;">Take Profit 1</p><input class="input" type="text" id="sig-tp1" placeholder="ex: 1.0920" style="font-family:'Geist Mono',monospace;border-color:rgba(52,211,153,.2);" oninput="updatePreview();calcRR()"></div>
        <div><p class="text-[10px] mb-1.5" style="color:#34d399;opacity:.7;">TP2 (optionnel)</p><input class="input" type="text" id="sig-tp2" placeholder="ex: 1.0960" style="font-family:'Geist Mono',monospace;"></div>
        <div><p class="text-[10px] mb-1.5" style="color:#f87171;">Stop Loss</p><input class="input" type="text" id="sig-sl" placeholder="ex: 1.0800" style="font-family:'Geist Mono',monospace;border-color:rgba(248,113,113,.2);" oninput="updatePreview();calcRR()"></div>
      </div>
      <div class="mb-3">
        <div class="flex items-center justify-between mb-1.5">
          <p class="text-[10px]" style="color:#52525b;">Analyse</p>
          <span class="text-[10px] font-medium" id="rr-display" style="color:#38bdf8;">R:R —</span>
        </div>
        <textarea class="input" id="sig-note" style="min-height:56px;font-size:12px;" placeholder="Setup, contexte, invalidation..." oninput="updatePreview()"></textarea>
      </div>
      <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Screenshot (optionnel)</p>
        <div class="upload-zone" onclick="document.getElementById('sig-img').click()">
          <svg width="18" height="18" fill="none" stroke="#3f3f46" viewBox="0 0 24 24" stroke-width="1.5" style="margin:0 auto 5px;"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
          <p class="text-xs" style="color:#52525b;">Glisser ou <span style="color:#38bdf8;">parcourir</span></p>
        </div>
        <input type="file" id="sig-img" accept="image/*" style="display:none;">
      </div>
    </div>

    <!-- Step 2 -->
    <div id="pub-s2" class="px-6 py-5 overflow-y-auto" style="max-height:55vh;display:none;">
      <div class="mb-4"><p class="text-[10px] mb-2" style="color:#52525b;">Catégories destinataires</p>
        <div class="flex flex-col gap-1.5">
          <label style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05);border-radius:8px;cursor:pointer;"><input type="checkbox" checked style="accent-color:#38bdf8;"><span style="width:7px;height:7px;border-radius:50%;background:#34d399;flex-shrink:0;"></span><span class="text-xs text-zinc-200 flex-1">Clients actifs</span><span class="text-[11px]" style="color:#52525b;">847</span></label>
          <label style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05);border-radius:8px;cursor:pointer;"><input type="checkbox" style="accent-color:#38bdf8;"><span style="width:7px;height:7px;border-radius:50%;background:#a78bfa;flex-shrink:0;"></span><span class="text-xs text-zinc-200 flex-1">Premium</span><span class="text-[11px]" style="color:#52525b;">312</span></label>
          <label style="display:flex;align-items:center;gap:10px;padding:9px 12px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.05);border-radius:8px;cursor:pointer;"><input type="checkbox" style="accent-color:#38bdf8;"><span style="width:7px;height:7px;border-radius:50%;background:#38bdf8;flex-shrink:0;"></span><span class="text-xs text-zinc-200 flex-1">Tous</span><span class="text-[11px]" style="color:#52525b;">3 247</span></label>
        </div>
      </div>
      <div class="grid grid-cols-2 gap-3 mb-3">
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Tag campagne</p><input class="input" type="text" placeholder="signal_eurusd_h4" style="font-size:12px;font-family:'Geist Mono',monospace;"></div>
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Delay envoi</p><input class="input" type="text" value="0.1s" style="font-size:12px;font-family:'Geist Mono',monospace;"></div>
      </div>
      <!-- Boutons participation -->
      <div style="padding:12px;background:rgba(52,211,153,.04);border:1px solid rgba(52,211,153,.12);border-radius:9px;">
        <p class="text-xs font-medium text-zinc-200 mb-2">Boutons de participation inclus automatiquement</p>
        <div class="flex gap-2 mb-2">
          <div class="tg-btn green" style="flex:1;font-size:11px;">✅ Je suis dans ce trade</div>
          <div class="tg-btn red" style="flex:1;font-size:11px;">❌ Je ne l'ai pas</div>
        </div>
        <p class="text-[10px]" style="color:#71717a;">Ces boutons enregistrent <code style="font-family:'Geist Mono',monospace;">participated: true/false</code> dans <code style="font-family:'Geist Mono',monospace;">trade_journal</code> et alimentent les stats d'engagement.</p>
      </div>
    </div>

    <!-- Step 3 : Aperçu complet -->
    <div id="pub-s3" class="px-6 py-5 overflow-y-auto" style="max-height:55vh;display:none;">
      <div class="grid grid-cols-2 gap-4">
        <!-- Aperçu Telegram -->
        <div>
          <p class="text-xs font-medium text-zinc-300 mb-3">Aperçu Telegram</p>
          <div class="tg-phone">
            <div class="tg-bar">
              <div style="width:26px;height:26px;border-radius:50%;background:#0ea5e9;display:flex;align-items:center;justify-content:center;flex-shrink:0;"><svg width="13" height="13" fill="white" viewBox="0 0 24 24"><path d="M12 0C5.37 0 0 5.37 0 12s5.37 12 12 12 12-5.37 12-12S18.63 0 12 0zm5.94 8.19l-2.02 9.52c-.14.66-.54.82-1.09.51l-3-2.21-1.45 1.39c-.16.16-.3.3-.61.3l.22-3.1 5.6-5.06c.24-.22-.06-.34-.38-.12L7.03 14.5 4.06 13.6c-.65-.2-.66-.65.14-.96l11.65-4.5c.54-.2 1.01.13.09 2.05z"/></svg></div>
              <div><p style="font-size:11px;font-weight:600;color:#e2e8f0;">TradingBot</p><p style="font-size:9px;color:#4a6478;">bot</p></div>
            </div>
            <div class="tg-msg-area">
              <div class="tg-bubble" id="tg-preview-msg">📊 <b>Signal de Trading</b><br><br>Remplis les champs pour voir l'aperçu...</div>
              <p class="tg-time">09:14 ✓✓</p>
              <div class="tg-inline-btns">
                <div class="tg-btn green">✅ Je suis dans ce trade</div>
                <div class="tg-btn red">❌ Je ne prends pas</div>
              </div>
            </div>
          </div>
        </div>
        <!-- Résumé -->
        <div>
          <p class="text-xs font-medium text-zinc-300 mb-3">Résumé</p>
          <div style="background:rgba(52,211,153,.04);border:1px solid rgba(52,211,153,.12);border-radius:9px;padding:12px;margin-bottom:10px;">
            <p class="text-xs font-medium text-zinc-200 mb-1.5">✓ Prêt à publier</p>
            <div class="flex flex-col gap-1">
              <p class="text-[11px]" style="color:#52525b;">→ Signal enregistré dans <code style="font-family:'Geist Mono',monospace;">signals</code></p>
              <p class="text-[11px]" style="color:#52525b;">→ Broadcast via <code style="font-family:'Geist Mono',monospace;">broadcast_engine</code></p>
              <p class="text-[11px]" style="color:#52525b;">→ Boutons inline Telegram attachés</p>
              <p class="text-[11px]" style="color:#52525b;">→ Participations enregistrées dans <code style="font-family:'Geist Mono',monospace;">trade_journal</code></p>
            </div>
          </div>
          <div style="background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.06);border-radius:9px;padding:12px;">
            <p class="text-[10px] mb-2" style="color:#52525b;">Formulaire de clôture</p>
            <p class="text-[11px]" style="color:#a1a1aa;line-height:1.6;">Après clôture du trade, un formulaire adapté au résultat (TP/SL/Partiel) sera proposé à l'envoi vers les membres "Je suis dedans".</p>
          </div>
        </div>
      </div>
    </div>

    <div class="flex items-center justify-between px-6 py-4" style="border-top:1px solid rgba(255,255,255,.06);">
      <button class="btn-ghost" id="btn-prev" onclick="prevStep()" style="display:none;">← Retour</button>
      <div class="flex-1"></div>
      <button class="btn-ghost" onclick="closeModal('modal-publish')">Annuler</button>
      <button class="btn-primary ml-2" id="btn-next" onclick="nextStep()">Continuer →</button>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════════════ -->
<!-- MODAL : CLÔTURER + CONFIRMATION FORMULAIRE                             -->
<!-- ══════════════════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="modal-close-trade">
  <div class="modal" style="width:500px;">
    <div class="flex items-center justify-between px-6 py-4" style="border-bottom:1px solid rgba(255,255,255,.06);">
      <div><p class="text-sm font-medium text-white">Clôturer le trade</p><p class="text-[11px] mt-0.5" style="color:#52525b;">EUR/USD LONG · Entrée 1.0842 · 237 membres "Je suis dedans"</p></div>
      <button class="btn-icon" onclick="closeModal('modal-close-trade')"><svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" d="M18 6 6 18M6 6l12 12"/></svg></button>
    </div>
    <div class="px-6 py-5 overflow-y-auto flex flex-col gap-4">

      <!-- Statut -->
      <div>
        <p class="text-[10px] mb-2" style="color:#52525b;">Résultat du trade</p>
        <div class="flex gap-2">
          <button id="close-tp" onclick="setCloseStatus('tp')" style="flex:1;padding:9px 6px;border-radius:8px;cursor:pointer;font-size:11px;font-family:'Geist',sans-serif;border:1px solid rgba(52,211,153,.3);background:rgba(52,211,153,.1);color:#34d399;font-weight:500;">✅ TP atteint</button>
          <button id="close-sl" onclick="setCloseStatus('sl')" style="flex:1;padding:9px 6px;border-radius:8px;cursor:pointer;font-size:11px;font-family:'Geist',sans-serif;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:#71717a;">❌ SL touché</button>
          <button id="close-partial" onclick="setCloseStatus('partial')" style="flex:1;padding:9px 6px;border-radius:8px;cursor:pointer;font-size:11px;font-family:'Geist',sans-serif;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:#71717a;">⚡ Partiel</button>
          <button id="close-cancel" onclick="setCloseStatus('cancel')" style="flex:1;padding:9px 6px;border-radius:8px;cursor:pointer;font-size:11px;font-family:'Geist',sans-serif;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:#71717a;">🚫 Annulé</button>
        </div>
      </div>

      <!-- Prix de clôture -->
      <div class="grid grid-cols-2 gap-3" id="price-block">
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Prix de clôture réel</p><input class="input" type="text" id="close-price" placeholder="ex: 1.0920" style="font-family:'Geist Mono',monospace;" oninput="calcPnL()"></div>
        <div><p class="text-[10px] mb-1.5" style="color:#52525b;">Résultat calculé</p>
          <div style="background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:7px 12px;display:flex;align-items:center;gap:8px;">
            <span class="text-sm font-medium tabular-nums" id="calc-pnl" style="color:#52525b;">—</span>
            <span class="text-xs" id="calc-pct" style="color:#52525b;"></span>
          </div>
        </div>
      </div>

      <!-- Formulaire de collecte — adaptatif selon statut -->
      <div id="form-collect-block" style="background:rgba(167,139,250,.05);border:1px solid rgba(167,139,250,.18);border-radius:10px;padding:14px;">
        <div class="flex items-center gap-2 mb-3">
          <svg width="13" height="13" fill="none" stroke="#a78bfa" viewBox="0 0 24 24" stroke-width="1.5"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/></svg>
          <p class="text-xs font-medium" style="color:#a78bfa;">Formulaire de collecte automatique</p>
        </div>

        <!-- Aperçu du formulaire selon statut -->
        <div id="form-preview" class="tg-phone mb-3">
          <div class="tg-bar">
            <div style="width:22px;height:22px;border-radius:50%;background:#0ea5e9;display:flex;align-items:center;justify-content:center;flex-shrink:0;"><svg width="11" height="11" fill="white" viewBox="0 0 24 24"><path d="M12 0C5.37 0 0 5.37 0 12s5.37 12 12 12 12-5.37 12-12S18.63 0 12 0zm5.94 8.19l-2.02 9.52c-.14.66-.54.82-1.09.51l-3-2.21-1.45 1.39c-.16.16-.3.3-.61.3l.22-3.1 5.6-5.06c.24-.22-.06-.34-.38-.12L7.03 14.5 4.06 13.6c-.65-.2-.66-.65.14-.96l11.65-4.5c.54-.2 1.01.13.09 2.05z"/></svg></div>
            <p style="font-size:10px;font-weight:600;color:#e2e8f0;">TradingBot</p>
          </div>
          <div class="tg-msg-area" id="form-msg-preview">
            <div class="tg-bubble" id="form-bbl">Sélectionne un résultat pour voir le formulaire...</div>
            <div class="tg-inline-btns" id="form-btns"></div>
          </div>
        </div>

        <!-- Options envoi -->
        <div class="flex flex-col gap-2">
          <p class="text-[10px] mb-1" style="color:#52525b;">Envoyer à :</p>
          <label class="flex items-center gap-2.5 text-xs text-zinc-300 cursor-pointer"><input type="radio" name="form-target" value="participated" checked style="accent-color:#38bdf8;"> <span>Membres "Je suis dedans" uniquement <span style="color:#52525b;">(237)</span></span></label>
          <label class="flex items-center gap-2.5 text-xs text-zinc-400 cursor-pointer"><input type="radio" name="form-target" value="all" style="accent-color:#38bdf8;"> <span>Tous les destinataires du signal <span style="color:#52525b;">(847)</span></span></label>
        </div>

        <div class="flex items-center justify-between mt-3 pt-3" style="border-top:1px solid rgba(167,139,250,.15);">
          <span class="text-xs text-zinc-400">Activer l'envoi</span>
          <button class="toggle on" id="form-toggle" onclick="this.classList.toggle('on')"></button>
        </div>
      </div>

    </div>
    <div class="flex items-center justify-end gap-2 px-6 py-4" style="border-top:1px solid rgba(255,255,255,.06);">
      <button class="btn-ghost" onclick="closeModal('modal-close-trade')">Annuler</button>
      <button class="btn-primary" onclick="closeModal('modal-close-trade')">Clôturer & envoyer formulaire</button>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════════════ -->
<!-- DRAWER : DÉTAIL SIGNAL                                                 -->
<!-- ══════════════════════════════════════════════════════════════════════ -->
<div class="drawer-overlay" id="drawer-overlay" onclick="closeAllDrawers()"></div>

<div class="drawer" id="signal-drawer" style="width:480px;">
  <div class="flex items-center justify-between px-5 py-4" style="border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0;">
    <div><p class="text-sm font-medium text-white">XAU/USD LONG</p><p class="text-[11px] mt-0.5" style="color:#52525b;">Hier 14:30 → 18:22 · H1 · +4.2%</p></div>
    <button class="btn-icon" onclick="closeAllDrawers()"><svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" d="M18 6 6 18M6 6l12 12"/></svg></button>
  </div>
  <div class="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">

    <!-- Niveaux -->
    <div>
      <p class="slabel">Niveaux admin</p>
      <div class="grid grid-cols-4 gap-2">
        <div class="stat-mini text-center"><p class="text-[9px] mb-1" style="color:#52525b;">Entrée</p><p class="text-sm tabular-nums text-white" style="font-family:'Geist Mono',monospace;">2 320</p></div>
        <div class="stat-mini text-center"><p class="text-[9px] mb-1" style="color:#34d399;">TP1</p><p class="text-sm tabular-nums pnl-pos" style="font-family:'Geist Mono',monospace;">2 387</p></div>
        <div class="stat-mini text-center"><p class="text-[9px] mb-1" style="color:#f87171;">SL</p><p class="text-sm tabular-nums pnl-neg" style="font-family:'Geist Mono',monospace;">2 295</p></div>
        <div class="stat-mini text-center"><p class="text-[9px] mb-1" style="color:#52525b;">R:R</p><p class="text-sm tabular-nums text-white">1:2.6</p></div>
      </div>
    </div>

    <!-- Participation -->
    <div>
      <p class="slabel">Participation au signal</p>
      <div class="part-bar mb-3" style="height:8px;">
        <div class="part-seg" style="width:28%;background:#34d399;border-radius:99px 0 0 99px;"></div>
        <div class="part-seg" style="width:15%;background:#f87171;"></div>
        <div class="part-seg" style="width:57%;background:rgba(255,255,255,.06);border-radius:0 99px 99px 0;"></div>
      </div>
      <div class="eng-grid">
        <div class="eng-cell" style="background:rgba(52,211,153,.07);border:1px solid rgba(52,211,153,.15);">
          <p class="text-lg font-light tabular-nums" style="color:#34d399;">237</p>
          <p class="text-[10px] mt-1" style="color:#34d399;opacity:.8;">✅ Suis le trade</p>
          <p class="text-[9px] mt-0.5" style="color:#52525b;">28% des destinataires</p>
        </div>
        <div class="eng-cell" style="background:rgba(248,113,113,.07);border:1px solid rgba(248,113,113,.15);">
          <p class="text-lg font-light tabular-nums" style="color:#f87171;">127</p>
          <p class="text-[10px] mt-1" style="color:#f87171;opacity:.8;">❌ Ne prend pas</p>
          <p class="text-[9px] mt-0.5" style="color:#52525b;">15% des destinataires</p>
        </div>
        <div class="eng-cell" style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);">
          <p class="text-lg font-light tabular-nums" style="color:#71717a;">483</p>
          <p class="text-[10px] mt-1" style="color:#52525b;">⏳ Pas répondu</p>
          <p class="text-[9px] mt-0.5" style="color:#52525b;">57% des destinataires</p>
        </div>
      </div>
    </div>

    <!-- Comportement à la clôture -->
    <div>
      <p class="slabel">Comportement à la clôture (189 réponses)</p>
      <div class="exit-bar">
        <div class="exit-row">
          <span style="color:#34d399;min-width:120px;font-size:11px;">A pris le TP ✓</span>
          <div class="exit-track"><div class="exit-fill" style="width:75%;background:#34d399;"></div></div>
          <span class="text-xs tabular-nums" style="color:#34d399;min-width:30px;text-align:right;">142</span>
        </div>
        <div class="exit-row">
          <span style="color:#fbbf24;min-width:120px;font-size:11px;">Sortie anticipée ⚡</span>
          <div class="exit-track"><div class="exit-fill" style="width:16%;background:#fbbf24;"></div></div>
          <span class="text-xs tabular-nums" style="color:#fbbf24;min-width:30px;text-align:right;">31</span>
        </div>
        <div class="exit-row">
          <span style="color:#f87171;min-width:120px;font-size:11px;">SL touché ✗</span>
          <div class="exit-track"><div class="exit-fill" style="width:8%;background:#f87171;"></div></div>
          <span class="text-xs tabular-nums" style="color:#f87171;min-width:30px;text-align:right;">16</span>
        </div>
        <div class="exit-row">
          <span style="color:#71717a;min-width:120px;font-size:11px;">N'était pas dedans</span>
          <div class="exit-track"><div class="exit-fill" style="width:0%;background:#71717a;"></div></div>
          <span class="text-xs tabular-nums" style="color:#71717a;min-width:30px;text-align:right;">0</span>
        </div>
      </div>
      <div class="flex gap-3 mt-3 text-[11px]">
        <span style="color:#34d399;">75% win rate membres</span>
        <span style="color:#38bdf8;">+3.8% perf. moy. réelle</span>
        <span style="color:#52525b;">vs +4.2% théorique</span>
      </div>
    </div>

    <!-- Top performers -->
    <div>
      <p class="slabel">Top 3 ce trade</p>
      <div class="flex flex-col gap-1">
        <div class="member-row"><div class="av av-violet" style="font-size:10px;">LB</div><div class="flex-1"><p class="text-xs text-zinc-200">Lucie Bernard</p><span class="beh-tag disciplined" style="font-size:9px;">Discipliné ✓</span></div><span class="text-xs tabular-nums pnl-pos">+4.1%</span></div>
        <div class="member-row"><div class="av av-green" style="font-size:10px;">MR</div><div class="flex-1"><p class="text-xs text-zinc-200">Marc Renaud</p><span class="beh-tag disciplined" style="font-size:9px;">Discipliné ✓</span></div><span class="text-xs tabular-nums pnl-pos">+3.9%</span></div>
        <div class="member-row"><div class="av av-sky" style="font-size:10px;">SA</div><div class="flex-1"><p class="text-xs text-zinc-200">Sophie Amar</p><span class="beh-tag early-exit" style="font-size:9px;">Sortie tôt ⚡</span></div><span class="text-xs tabular-nums pnl-pos">+2.1%</span></div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════════════ -->
<!-- DRAWER : PROFIL MEMBRE                                                 -->
<!-- ══════════════════════════════════════════════════════════════════════ -->
<div class="drawer" id="member-drawer" style="width:420px;">
  <div class="flex items-center justify-between px-5 py-4" style="border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0;">
    <div class="flex items-center gap-3">
      <div class="av av-green" style="width:38px;height:38px;font-size:13px;">MR</div>
      <div><p class="text-sm font-medium text-white">Marc Renaud</p><p class="text-[11px] mt-0.5" style="color:#52525b;">@marc_renaud · ID 1042</p></div>
    </div>
    <button class="btn-icon" onclick="closeAllDrawers()"><svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" d="M18 6 6 18M6 6l12 12"/></svg></button>
  </div>
  <div class="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">

    <!-- Stats globales -->
    <div class="grid grid-cols-4 gap-2">
      <div class="stat-mini text-center"><p class="text-base font-light text-white tabular-nums">12</p><p class="text-[9px] mt-1" style="color:#52525b;">Trades</p></div>
      <div class="stat-mini text-center"><p class="text-base font-light tabular-nums pnl-pos">71%</p><p class="text-[9px] mt-1" style="color:#52525b;">Win</p></div>
      <div class="stat-mini text-center"><p class="text-base font-light tabular-nums" style="color:#38bdf8;">83%</p><p class="text-[9px] mt-1" style="color:#52525b;">Engag.</p></div>
      <div class="stat-mini text-center"><p class="text-base font-light tabular-nums pnl-pos">+33.6%</p><p class="text-[9px] mt-1" style="color:#52525b;">Total</p></div>
    </div>

    <!-- Comportement détaillé -->
    <div>
      <p class="slabel">Profil comportemental</p>
      <div class="flex flex-wrap gap-2 mb-3">
        <span class="beh-tag disciplined">Suit les SL ✓</span>
        <span class="beh-tag disciplined">Attend le TP ✓</span>
        <span class="badge badge-sky" style="font-size:11px;">Engagement fort</span>
      </div>
      <div class="exit-bar">
        <div class="exit-row">
          <span style="color:#34d399;min-width:110px;font-size:11px;">A pris le TP</span>
          <div class="exit-track"><div class="exit-fill" style="width:71%;background:#34d399;"></div></div>
          <span class="text-xs" style="color:#34d399;">71%</span>
        </div>
        <div class="exit-row">
          <span style="color:#fbbf24;min-width:110px;font-size:11px;">Sortie anticipée</span>
          <div class="exit-track"><div class="exit-fill" style="width:17%;background:#fbbf24;"></div></div>
          <span class="text-xs" style="color:#fbbf24;">17%</span>
        </div>
        <div class="exit-row">
          <span style="color:#f87171;min-width:110px;font-size:11px;">SL touché</span>
          <div class="exit-track"><div class="exit-fill" style="width:12%;background:#f87171;"></div></div>
          <span class="text-xs" style="color:#f87171;">12%</span>
        </div>
      </div>
      <div class="mt-3 p-2.5" style="background:rgba(255,255,255,.025);border-radius:7px;">
        <p class="text-[10px]" style="color:#52525b;">Perf. théorique (si TP systématique) : <span style="color:#34d399;">+38.4%</span> vs réelle <span style="color:#34d399;">+33.6%</span></p>
        <p class="text-[10px] mt-0.5" style="color:#52525b;">Manque à gagner sorties anticipées : <span style="color:#fbbf24;">-4.8%</span></p>
      </div>
    </div>

    <!-- Historique trades -->
    <div>
      <p class="slabel">Derniers trades journalisés</p>
      <div class="flex flex-col gap-1">
        <div class="member-row"><div style="width:7px;height:7px;border-radius:50%;background:#34d399;flex-shrink:0;"></div><span class="text-xs text-zinc-300 flex-1" style="font-family:'Geist Mono',monospace;">XAU/USD LONG</span><span class="beh-tag disciplined" style="font-size:9px;">TP ✓</span><span class="text-xs tabular-nums pnl-pos ml-2">+4.1%</span></div>
        <div class="member-row"><div style="width:7px;height:7px;border-radius:50%;background:#34d399;flex-shrink:0;"></div><span class="text-xs text-zinc-300 flex-1" style="font-family:'Geist Mono',monospace;">BTC/USD SHORT</span><span class="beh-tag disciplined" style="font-size:9px;">TP ✓</span><span class="text-xs tabular-nums pnl-pos ml-2">+2.8%</span></div>
        <div class="member-row"><div style="width:7px;height:7px;border-radius:50%;background:#fbbf24;flex-shrink:0;"></div><span class="text-xs text-zinc-300 flex-1" style="font-family:'Geist Mono',monospace;">EUR/USD LONG</span><span class="beh-tag early-exit" style="font-size:9px;">Sortie tôt</span><span class="text-xs tabular-nums pnl-pos ml-2">+1.2%</span></div>
        <div class="member-row"><div style="width:7px;height:7px;border-radius:50%;background:#f87171;flex-shrink:0;"></div><span class="text-xs text-zinc-300 flex-1" style="font-family:'Geist Mono',monospace;">GBP/JPY SHORT</span><span class="beh-tag sl-skip" style="font-size:9px;">SL ✗</span><span class="text-xs tabular-nums pnl-neg ml-2">-1.4%</span></div>
      </div>
    </div>

    <!-- Recommandations IA -->
    <div class="ia-card">
      <div class="flex items-center gap-2 mb-3"><span class="ia-chip">IA</span><p class="text-xs font-medium text-zinc-200">Recommandations</p></div>
      <div style="font-size:12px;color:#a1a1aa;line-height:1.7;">
        ✅ Excellent engagement cette semaine (83%).<br><br>
        📌 Tu laisses parfois 1-2% sur la table en sortant avant le TP — tes sorties anticipées t'ont coûté <span style="color:#fbbf24;">-4.8%</span> sur la période.<br><br>
        💡 Recommendation : essaie de rester en position jusqu'au TP1 sur les signaux H1. Ta discipline sur le SL est parfaite, applique la même rigueur au TP.
      </div>
    </div>
  </div>
  <div class="flex items-center gap-2 px-5 py-4" style="border-top:1px solid rgba(255,255,255,.06);flex-shrink:0;">
    <button class="btn-ghost" style="flex:1;justify-content:center;font-size:11px;">Chat direct</button>
    <button class="btn-primary" style="flex:1;justify-content:center;font-size:11px;">Envoyer bilan IA</button>
  </div>
</div>

<!-- Modal send bilan -->
<div class="modal-overlay" id="modal-send-bilan">
  <div class="modal" style="width:400px;">
    <div class="flex items-center justify-between px-6 py-4" style="border-bottom:1px solid rgba(255,255,255,.06);">
      <p class="text-sm font-medium text-white">Confirmer l'envoi</p>
      <button class="btn-icon" onclick="closeModal('modal-send-bilan')"><svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"><path stroke-linecap="round" d="M18 6 6 18M6 6l12 12"/></svg></button>
    </div>
    <div class="px-6 py-5 flex flex-col gap-3">
      <div style="padding:10px 12px;background:rgba(255,255,255,.025);border-radius:8px;"><p class="text-xs text-zinc-200">189 bilans personnalisés</p><p class="text-[11px] mt-1" style="color:#52525b;">Semaine #17 · Incluant analyse comportementale</p></div>
      <div style="padding:10px 12px;background:rgba(45,212,191,.05);border:1px solid rgba(45,212,191,.15);border-radius:8px;"><p class="text-xs" style="color:#2dd4bf;">Chaque bilan inclut le profil comportemental du membre (respect SL/TP, sorties anticipées) et des recommandations personnalisées.</p></div>
    </div>
    <div class="flex items-center justify-end gap-2 px-6 py-4" style="border-top:1px solid rgba(255,255,255,.06);">
      <button class="btn-ghost" onclick="closeModal('modal-send-bilan')">Annuler</button>
      <button class="btn-primary" onclick="closeModal('modal-send-bilan')">Envoyer 189 bilans →</button>
    </div>
  </div>
</div>

<!-- ════════════════════════════════════════════════════════════════════ -->
<!-- JS                                                                   -->
<!-- ════════════════════════════════════════════════════════════════════ -->
<script>

// ── Vues ──────────────────────────────────────────────────────────────────
function switchView(view, el) {
  ['journal','members','leaderboard','ia'].forEach(v => {
    const e = document.getElementById('view-'+v)
    if (e) e.style.display = 'none'
  })
  const t = document.getElementById('view-'+view)
  if (t) t.style.display = 'block'
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'))
  if (el) el.classList.add('active')
}

// ── Publish steps ──────────────────────────────────────────────────────────
let pubStep = 1

function nextStep() {
  if (pubStep === 1) {
    if (!document.getElementById('sig-entry').value.trim()) { document.getElementById('sig-entry').focus(); return }
    goPubStep(2)
  } else if (pubStep === 2) {
    updatePreview()
    goPubStep(3)
    document.getElementById('btn-next').textContent = '📡 Publier le signal'
    document.getElementById('btn-next').style.cssText += ';background:#34d399;color:#052e16;'
  } else {
    // API: POST /signals + broadcast_engine
    closeModal('modal-publish')
    resetPubModal()
  }
}

function prevStep() {
  if (pubStep > 1) {
    goPubStep(pubStep - 1)
    document.getElementById('btn-next').textContent = 'Continuer →'
    document.getElementById('btn-next').style.background = ''
    document.getElementById('btn-next').style.color = ''
  }
}

function goPubStep(n) {
  pubStep = n
  ;[1,2,3].forEach(i => {
    document.getElementById('pub-s'+i).style.display = i === n ? 'block' : 'none'
    const dot = document.getElementById('sdot-'+i)
    const lbl = document.getElementById('slbl-'+i)
    dot.className = 'step-dot' + (i < n ? ' done' : i === n ? ' active' : '')
    lbl.style.color = i === n ? '#38bdf8' : i < n ? '#34d399' : '#3f3f46'
  })
  document.getElementById('btn-prev').style.display = n > 1 ? 'inline-flex' : 'none'
}

function resetPubModal() {
  pubStep = 1; goPubStep(1)
  document.getElementById('btn-next').textContent = 'Continuer →'
  document.getElementById('btn-next').style.background = ''
  document.getElementById('btn-next').style.color = ''
}

// ── Direction ─────────────────────────────────────────────────────────────
let tradeDir = 'long'
function setDir(d) {
  tradeDir = d
  const L = document.getElementById('dir-long'), S = document.getElementById('dir-short')
  const base = 'flex:1;padding:7px;border-radius:8px;cursor:pointer;font-size:12px;font-family:Geist,sans-serif;'
  L.style.cssText = base + (d==='long' ? 'border:1px solid rgba(52,211,153,.3);background:rgba(52,211,153,.1);color:#34d399;font-weight:500;' : 'border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.04);color:#71717a;')
  S.style.cssText = base + (d==='short' ? 'border:1px solid rgba(248,113,113,.3);background:rgba(248,113,113,.1);color:#f87171;font-weight:500;' : 'border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.04);color:#71717a;')
  updatePreview()
}

// ── R:R & preview ────────────────────────────────────────────────────────
function calcRR() {
  const e = parseFloat(document.getElementById('sig-entry')?.value)
  const t = parseFloat(document.getElementById('sig-tp1')?.value)
  const s = parseFloat(document.getElementById('sig-sl')?.value)
  const el = document.getElementById('rr-display')
  if (!isNaN(e) && !isNaN(t) && !isNaN(s) && Math.abs(e-s) > 0) {
    el.textContent = `R:R 1:${(Math.abs(t-e)/Math.abs(e-s)).toFixed(1)}`
    el.style.color = '#38bdf8'
  } else {
    el.textContent = 'R:R —'; el.style.color = '#52525b'
  }
}

function updatePreview() {
  const pair  = document.getElementById('sig-pair')?.value || '—'
  const entry = document.getElementById('sig-entry')?.value || '—'
  const tp1   = document.getElementById('sig-tp1')?.value  || '—'
  const sl    = document.getElementById('sig-sl')?.value   || '—'
  const note  = document.getElementById('sig-note')?.value || ''
  const dir   = tradeDir === 'long' ? '📈 LONG' : '📉 SHORT'
  const msg   = document.getElementById('tg-preview-msg')
  if (msg) msg.innerHTML = `📊 <b>Signal de Trading</b><br><br>🔷 Paire : <b>${pair}</b><br>${tradeDir==='long'?'📈':'📉'} Direction : <b>${dir}</b><br>🎯 Entrée : <b>${entry}</b><br>✅ TP1 : <b>${tp1}</b><br>❌ SL : <b>${sl}</b>${note ? `<br><br><i>${note}</i>` : ''}`
}

// ── Clôture trade ─────────────────────────────────────────────────────────
let closeStatus = 'tp'

// Formulaires adaptatifs par statut
const FORM_TEMPLATES = {
  tp: {
    msg: `🎉 <b>TP1 atteint sur EUR/USD ! (+4.2%)</b><br><br>As-tu clôturé ce trade ?`,
    btns: [
      { text: '✅ Oui, j\'ai pris le TP', cls: 'green' },
      { text: '🔄 Je suis encore dedans', cls: '' },
      { text: '❌ J\'ai coupé en perte', cls: 'red' },
    ]
  },
  sl: {
    msg: `⚠️ <b>SL touché sur EUR/USD (-1.8%)</b><br><br>As-tu respecté le stop ?`,
    btns: [
      { text: '✅ Oui, j\'ai coupé au SL', cls: 'green' },
      { text: '📈 J\'ai gardé la position', cls: 'amber' },
      { text: '🚫 Je n\'étais pas dedans', cls: 'gray' },
    ]
  },
  partial: {
    msg: `⚡ <b>Trade EUR/USD clôturé partiellement</b><br><br>Où en es-tu ?`,
    btns: [
      { text: '✅ J\'ai pris mon TP partiel', cls: 'green' },
      { text: '🔄 Je reste en position', cls: '' },
      { text: '❌ J\'ai tout coupé', cls: 'red' },
    ]
  },
  cancel: {
    msg: `ℹ️ <b>Signal EUR/USD annulé</b><br><br>Le trade n\'a pas été déclenché.`,
    btns: [
      { text: '✅ Je n\'avais pas pris', cls: 'green' },
      { text: '⚠️ J\'avais déjà ouvert', cls: 'amber' },
    ]
  }
}

function setCloseStatus(s) {
  closeStatus = s
  const statuses = ['tp','sl','partial','cancel']
  statuses.forEach(id => {
    const btn = document.getElementById('close-'+id)
    if (!btn) return
    const configs = {
      tp:      { border:'rgba(52,211,153,.3)',  bg:'rgba(52,211,153,.1)',  color:'#34d399' },
      sl:      { border:'rgba(248,113,113,.3)', bg:'rgba(248,113,113,.1)', color:'#f87171' },
      partial: { border:'rgba(251,191,36,.3)',  bg:'rgba(251,191,36,.1)',  color:'#fbbf24' },
      cancel:  { border:'rgba(113,113,122,.3)', bg:'rgba(113,113,122,.1)', color:'#a1a1aa' },
    }
    if (id === s) {
      const c = configs[s]
      btn.style.cssText = `flex:1;padding:9px 6px;border-radius:8px;cursor:pointer;font-size:11px;font-family:'Geist',sans-serif;border:1px solid ${c.border};background:${c.bg};color:${c.color};font-weight:500;`
    } else {
      btn.style.cssText = `flex:1;padding:9px 6px;border-radius:8px;cursor:pointer;font-size:11px;font-family:'Geist',sans-serif;border:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03);color:#71717a;`
    }
  })

  // Prix visible sauf annulé
  const pb = document.getElementById('price-block')
  if (pb) pb.style.display = s === 'cancel' ? 'none' : 'grid'

  // Mettre à jour l'aperçu du formulaire
  const tpl = FORM_TEMPLATES[s]
  if (tpl) {
    document.getElementById('form-bbl').innerHTML = tpl.msg
    const btnsEl = document.getElementById('form-btns')
    btnsEl.innerHTML = ''
    tpl.btns.forEach(b => {
      const el = document.createElement('div')
      el.className = `tg-btn ${b.cls}`
      el.style.fontSize = '11px'
      el.textContent = b.text
      btnsEl.appendChild(el)
    })
  }
  calcPnL()
}

function calcPnL() {
  const entry = 1.0842
  const close = parseFloat(document.getElementById('close-price')?.value)
  const pnl   = document.getElementById('calc-pnl')
  const pct   = document.getElementById('calc-pct')
  if (!isNaN(close) && close > 0) {
    const diff = ((close - entry) / entry * 100)
    const pips = Math.round((close - entry) * 10000)
    const pos  = diff >= 0
    pnl.textContent = `${pos?'+':''}${pips} pips`
    pct.textContent = `(${pos?'+':''}${diff.toFixed(2)}%)`
    pnl.style.color = pct.style.color = pos ? '#34d399' : '#f87171'
  } else {
    pnl.textContent = '—'; pnl.style.color = '#52525b'
    pct.textContent = ''
  }
}

// ── IA bilan ──────────────────────────────────────────────────────────────
function generateBilan() {
  const box = document.getElementById('ia-preview-box')
  const status = document.getElementById('ia-status')
  box.style.color = '#3f3f46'; box.textContent = 'Génération en cours...'
  status.textContent = 'Génération...'
  setTimeout(() => {
    status.textContent = 'Généré ✓'
    box.style.color = '#a1a1aa'; box.style.fontStyle = 'normal'
    box.innerHTML = `Bonjour <b style="color:#e4e4e7;">Marc</b> 👋<br><br><b style="color:#e4e4e7;">Bilan semaine #17</b><br><br>📊 <span style="color:#34d399;">12 trades · 71% win rate · +33.6% total</span><br>📈 Engagement : tu as répondu à <span style="color:#38bdf8;">83%</span> des signaux.<br><br>🏆 Meilleur trade : <span style="color:#34d399;">XAU/USD +4.1%</span><br><br>📌 <b style="color:#e4e4e7;">Point d'amélioration :</b> Tes sorties anticipées t'ont coûté <span style="color:#fbbf24;">-4.8%</span> sur la période. Tu as le bon instinct — fais confiance aux niveaux.<br><br>💡 <b style="color:#e4e4e7;">Objectif semaine prochaine :</b> rester en position jusqu'au TP1 sur au moins 3 trades.`
  }, 1100)
}

// ── Drawers ───────────────────────────────────────────────────────────────
function openSignalDrawer() {
  document.getElementById('signal-drawer').classList.add('open')
  document.getElementById('drawer-overlay').classList.add('open')
}
function openMemberDrawer() {
  document.getElementById('member-drawer').classList.add('open')
  document.getElementById('drawer-overlay').classList.add('open')
}
function closeAllDrawers() {
  document.getElementById('signal-drawer').classList.remove('open')
  document.getElementById('member-drawer').classList.remove('open')
  document.getElementById('drawer-overlay').classList.remove('open')
}

// ── Modals ────────────────────────────────────────────────────────────────
function openModal(id)  { document.getElementById(id)?.classList.add('open') }
function closeModal(id) { document.getElementById(id)?.classList.remove('open') }
document.querySelectorAll('.modal-overlay').forEach(o => o.addEventListener('click', e => { if (e.target === o) o.classList.remove('open') }))
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return
  closeAllDrawers()
  document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('open'))
})

</script>
</body>
</html>