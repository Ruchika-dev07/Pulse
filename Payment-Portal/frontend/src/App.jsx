import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import Payments from "./Payments.jsx";
import SecureSite from "./SecureSite.jsx";
import { HeartFace, LogoMark, SIM_OPTIONS } from "./shared.jsx";

const TABS = [
  { id: "site", label: "Secure site" },
  { id: "payments", label: "Payments" },
];
const CLAIM_KEY = "pulse.pendingClaim";

const readTab = () => (window.location.hash === "#payments" ? "payments" : "site");

export default function App() {
  const [tab, setTab] = useState(readTab);
  const [health, setHealth] = useState(null);
  const [backendDown, setBackendDown] = useState(false);
  const [session, setSession] = useState(null);
  const [simOutcome, setSimOutcome] = useState("authorized");
  const [busy, setBusy] = useState(false);
  const [siteError, setSiteError] = useState("");
  const [pendingClaim, setPendingClaim] = useState(() => {
    try {
      return JSON.parse(sessionStorage.getItem(CLAIM_KEY) || "null");
    } catch {
      return null;
    }
  });
  const claiming = useRef(false);

  const savePending = (value) => {
    setPendingClaim(value);
    try {
      if (value) sessionStorage.setItem(CLAIM_KEY, JSON.stringify(value));
      else sessionStorage.removeItem(CLAIM_KEY);
    } catch {
      /* storage unavailable: keep it in memory only */
    }
  };

  /* tabs follow the URL hash so #payments can be linked */
  useEffect(() => {
    const onHash = () => setTab(readTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const goTab = (id) => {
    window.location.hash = id;
    setTab(id);
  };

  /* health, every 4 s */
  const loadHealth = useCallback(
    () =>
      api
        .health()
        .then((h) => {
          setHealth(h);
          setBackendDown(false);
        })
        .catch(() => setBackendDown(true)),
    []
  );
  useEffect(() => {
    loadHealth();
    const t = setInterval(loadHealth, 4000);
    return () => clearInterval(t);
  }, [loadHealth]);

  /* session state, every 700 ms: drives the header heart and the secure site */
  const loadSession = useCallback(() => api.session().then(setSession).catch(() => {}), []);
  useEffect(() => {
    loadSession();
    const t = setInterval(loadSession, 700);
    return () => clearInterval(t);
  }, [loadSession]);

  /* once the wearable approves our unlock, claim the session cookie */
  useEffect(() => {
    if (!session || !pendingClaim || claiming.current) return;
    const s = session.session;
    if (session.status === "active" && s && s.request_id === pendingClaim.request_id && !session.has_access) {
      if (s.claimed) {
        savePending(null);
        return;
      }
      claiming.current = true;
      api
        .claim(pendingClaim.request_id, pendingClaim.claim)
        .then((st) => setSession(st))
        .catch((err) => setSiteError(err.message))
        .finally(() => {
          claiming.current = false;
          savePending(null);
        });
    } else if (session.status === "locked" && Date.now() - (pendingClaim.at || 0) > 2000) {
      // unlock failed, timed out or was cancelled (ignore polls that started before the unlock)
      savePending(null);
    }
  }, [session, pendingClaim]);

  const unlock = async () => {
    setBusy(true);
    setSiteError("");
    try {
      const r = await api.unlock(health?.device?.mode === "sim" ? simOutcome : undefined);
      savePending({ request_id: r.request_id, claim: r.claim, at: Date.now() });
      await loadSession();
    } catch (err) {
      setSiteError(backendDown ? "Can’t reach the backend. Start it with: uvicorn main:app --reload --port 8000" : err.message);
    } finally {
      setBusy(false);
    }
  };

  const cancelUnlock = async () => {
    try {
      await api.cancelUnlock();
    } catch {
      /* ignore */
    }
    savePending(null);
    loadSession();
  };

  const lock = async () => {
    setBusy(true);
    try {
      await api.lock();
    } catch (err) {
      setSiteError(err.message);
    } finally {
      setBusy(false);
      loadSession();
    }
  };

  const unlocked = !!session?.has_access;
  const bpm = session?.wearable?.bpm;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <LogoMark />
          <span className="brand-name">Pulse</span>
        </div>

        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <a
              key={t.id}
              href={`#${t.id}`}
              className={`tab ${tab === t.id ? "is-on" : ""}`}
              aria-current={tab === t.id ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                goTab(t.id);
              }}
            >
              {t.label}
            </a>
          ))}
        </nav>

        <div className="pills">
          <div className={`mood ${unlocked ? "is-happy" : "is-sad"}`} title={unlocked ? "Unlocked" : "Locked"}>
            <HeartFace mood={unlocked ? "happy" : "sad"} size={26} bpm={bpm} />
            <span>{unlocked ? (bpm ? `Unlocked, ${bpm} bpm` : "Unlocked") : "Locked"}</span>
          </div>
          {health && !backendDown && <StoragePill storage={health.storage} />}
          <DevicePill health={health} backendDown={backendDown} />
        </div>
      </header>

      <main>
        {tab === "site" ? (
          <SecureSite
            session={session}
            health={health}
            pendingClaim={pendingClaim}
            onUnlock={unlock}
            onCancel={cancelUnlock}
            onLock={lock}
            busy={busy}
            error={siteError}
          />
        ) : (
          <Payments health={health} backendDown={backendDown} simOutcome={simOutcome} />
        )}
      </main>

      {health?.device?.mode === "sim" && (
        <DemoControls value={simOutcome} onChange={setSimOutcome} sim={health.device.sim} onSimChange={loadHealth} />
      )}
    </div>
  );
}

function StoragePill({ storage }) {
  if (!storage) return null;
  const mongo = storage.kind === "mongodb";
  const tone = mongo ? "ok" : storage.error ? "bad" : "muted";
  const text = mongo ? "MongoDB Atlas" : storage.error ? "Database offline" : "In-memory storage";
  const hint = mongo
    ? "Payments, sessions and receipts are saved to MongoDB Atlas"
    : storage.error || "Set MONGODB_URI in backend/.env to save to MongoDB Atlas";
  return (
    <div className={`pill is-${tone}`} title={hint}>
      <span className="pill-dot" aria-hidden="true" />
      {text}
    </div>
  );
}

function DevicePill({ health, backendDown }) {
  let tone = "muted";
  let text = "Connecting…";
  let hint = "";
  if (backendDown) {
    tone = "bad";
    text = "Backend offline";
    hint = "Run: uvicorn main:app --reload --port 8000";
  } else if (health) {
    const d = health.device;
    if (d.mode === "sim") {
      tone = "warn";
      text = "Simulated device";
      hint = "Set DEVICE_MODE=serial and SERIAL_PORT to use the real ESP32";
    } else if (d.connected) {
      tone = "ok";
      text = `Device on ${d.port}`;
    } else {
      tone = "bad";
      text = d.port ? `No device on ${d.port}` : "No serial port set";
      hint = d.error || "";
    }
  }
  return (
    <div className={`pill is-${tone}`} title={hint}>
      <span className="pill-dot" aria-hidden="true" />
      {text}
    </div>
  );
}

function DemoControls({ value, onChange, sim, onSimChange }) {
  const [open, setOpen] = useState(false);
  const [local, setLocal] = useState(sim || { worn: true, live: true });

  useEffect(() => {
    if (sim) setLocal(sim);
  }, [sim?.worn, sim?.live]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (field) => {
    const next = { ...local, [field]: !local[field] };
    setLocal(next);
    try {
      await api.setSim({ [field]: next[field] });
      onSimChange();
    } catch {
      setLocal(local);
    }
  };

  return (
    <div className={`demo ${open ? "is-open" : ""}`}>
      <button className="demo-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open
          ? "Simulator"
          : `Simulator: ${local.worn ? "band on" : "band off"}, ${SIM_OPTIONS.find((o) => o.value === value)?.label}`}
      </button>
      {open && (
        <div className="demo-body">
          <p>The simulated wearable, for demos without the hardware.</p>
          <div className="switches">
            <Switch label="Wearing the band" on={local.worn} onClick={() => toggle("worn")} />
            <Switch label="Heartbeat" on={local.live} onClick={() => toggle("live")} />
          </div>
          <p className="demo-sub">Next fingerprint check</p>
          <div role="radiogroup" aria-label="Simulated device outcome">
            {SIM_OPTIONS.map((o) => (
              <label key={o.value} className={`demo-opt ${value === o.value ? "is-on" : ""}`}>
                <input type="radio" name="sim" value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
                {o.label}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Switch({ label, on, onClick }) {
  return (
    <button type="button" role="switch" aria-checked={on} className={`switch ${on ? "is-on" : ""}`} onClick={onClick}>
      <span className="switch-track" aria-hidden="true">
        <span className="switch-knob" />
      </span>
      {label}
    </button>
  );
}
