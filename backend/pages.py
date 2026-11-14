"""Server-rendered HTML for /secret. Refreshes every 2 s so it flips live when the band comes off."""

from html import escape

END_REASONS = {
    "removed": "The wearable was taken off.",
    "no_heartbeat": "The wearable stopped reading a heartbeat.",
    "signal_lost": "The wearable stopped responding.",
    "locked": "The session was locked.",
    "expired": "The session reached its time limit.",
}

HAPPY = """<svg class="heart happy" viewBox="0 0 120 110" aria-hidden="true">
<path d="M60 104S6 72 6 36A27 27 0 0 1 60 22a27 27 0 0 1 54 14c0 36-54 68-54 68z" fill="#ff6b8b"/>
<circle cx="44" cy="46" r="5" fill="#2a0a14"/><circle cx="76" cy="46" r="5" fill="#2a0a14"/>
<path d="M46 62q14 14 28 0" fill="none" stroke="#2a0a14" stroke-width="5" stroke-linecap="round"/>
<circle cx="34" cy="60" r="5" fill="#ff9fb3"/><circle cx="86" cy="60" r="5" fill="#ff9fb3"/></svg>"""

SAD = """<svg class="heart sad" viewBox="0 0 120 110" aria-hidden="true">
<path d="M60 104S6 72 6 36A27 27 0 0 1 60 22a27 27 0 0 1 54 14c0 36-54 68-54 68z" fill="#5f6d8c"/>
<path d="M60 22l-5 9 6 5-4 7" fill="none" stroke="#0f1a2e" stroke-width="4" stroke-linejoin="round"/>
<path d="M38 44l10 4M82 44l-10 4" stroke="#0f1a2e" stroke-width="4" stroke-linecap="round"/>
<path d="M47 70q13-11 26 0" fill="none" stroke="#0f1a2e" stroke-width="5" stroke-linecap="round"/>
<path d="M40 54q-3 8 0 10q3-2 0-10z" fill="#9ec5ff"/></svg>"""

STYLE = """
:root{--bg:#0f1a2e;--surface:#16233d;--line:#2a3b5e;--text:#eef1f7;--muted:#95a2bd;--pulse:#ff6b8b;--ok:#4fd1a1}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
color:var(--text);font-family:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;padding:24px}
main{max-width:560px;width:100%;text-align:center}
.heart{width:132px;height:121px;margin-bottom:12px}
.happy{animation:beat 1s ease-in-out infinite}
@keyframes beat{0%,100%{transform:scale(1)}15%{transform:scale(1.08)}30%{transform:scale(.98)}}
.code{font-size:4.5rem;font-weight:700;letter-spacing:-.04em;margin:0;line-height:1}
h1{font-size:1.6rem;margin:10px 0 8px}p{color:var(--muted);margin:0 0 8px}
.card{margin-top:24px;text-align:left;background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px 18px}
.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--line);font-size:.92rem}
.row:last-child{border-bottom:0}.k{color:var(--muted)}.ok{color:var(--ok)}
a{color:var(--pulse)}
@media (prefers-reduced-motion:reduce){.happy{animation:none}}
"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="2"><title>{escape(title)}</title><style>{STYLE}</style></head>
<body><main>{body}</main></body></html>"""


def forbidden(state: dict) -> str:
    reason = ""
    end = state.get("last_end")
    if end:
        reason = f"<p>{escape(END_REASONS.get(end['reason'], 'The session ended.'))}</p>"
    status = state.get("status")
    hint = (
        "Unlocking now. Follow the steps on your wearable."
        if status == "unlocking"
        else "Put on your Pulse wearable and unlock from the Pulse app at localhost:5173."
    )
    return _page(
        "403 Forbidden",
        f"""{SAD}<p class="code">403</p><h1>No access</h1>{reason}<p>{escape(hint)}</p>""",
    )


def granted(state: dict, vault: dict) -> str:
    w = state.get("wearable") or {}
    bpm = f"{w['bpm']} bpm" if w.get("bpm") else "reading"
    rows = "".join(
        f'<div class="row"><span>{escape(i["key"])} {escape(i["title"])}</span><span class="k">{escape(i["state"])}</span></div>'
        for i in vault["issues"]
    )
    return _page(
        "Pulse: access granted",
        f"""{HAPPY}<h1>{escape(vault["message"])}</h1>
<p>Your wearable is on and your heart is beating at <span class="ok">{escape(bpm)}</span>.
Take it off and this page locks itself.</p>
<div class="card"><div class="row"><strong>{escape(vault["project"])}</strong><span class="k">{len(vault["issues"])} issues</span></div>{rows}</div>""",
    )
