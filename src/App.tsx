import { useEffect, useState } from "react";
import {
  Activity, Archive, ArrowDownToLine, ArrowRight, AudioLines, Clapperboard,
  Clock3, FolderOpen, Gauge, Layers3, Plus, Settings2, Sparkles, Video,
  WandSparkles, Zap
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8765";

type Health = "checking" | "online" | "offline";

const navItems = [
  { label: "Overview", icon: Gauge },
  { label: "Projects", icon: FolderOpen },
  { label: "AI Workspace", icon: Sparkles },
  { label: "Clip Editor", icon: Clapperboard },
  { label: "Subtitle Studio", icon: AudioLines },
  { label: "Export Center", icon: ArrowDownToLine }
];

function App() {
  const [active, setActive] = useState("Overview");
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/api/health`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(() => setHealth("online"))
      .catch(() => setHealth("offline"));
    return () => controller.abort();
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Clapperboard size={19} /></div>
          <div><strong>clipper<span>.</span></strong><small>PERSONAL STUDIO</small></div>
        </div>
        <div className="workspace-label">WORKSPACE</div>
        <nav>
          {navItems.map(({ label, icon: Icon }) => (
            <button key={label} className={active === label ? "nav-item active" : "nav-item"} onClick={() => setActive(label)}>
              <Icon size={17} strokeWidth={1.8} /><span>{label}</span>
              {label === "AI Workspace" && <span className="nav-tag">AI</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="storage-card">
            <div className="storage-title"><Layers3 size={15} /> Local workspace</div>
            <p>Your projects stay on this device.</p>
            <div className="storage-line"><span /></div>
            <small>Storage usage will appear here</small>
          </div>
          <button className="nav-item settings" onClick={() => setActive("Settings")}><Settings2 size={17} /><span>Settings</span></button>
          <div className="profile-row"><div className="avatar">P</div><div><strong>Personal workspace</strong><small>Local device</small></div><span className="profile-dots">•••</span></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> <strong>{active}</strong></div>
          <div className="topbar-right">
            <div className={`service-status ${health}`}><i />{health === "online" ? "Local service connected" : health === "checking" ? "Connecting local service" : "Local service offline"}</div>
            <div className="top-avatar">P</div>
          </div>
        </header>

        <div className="content">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> YOUR VIDEO WORKSPACE</div>
              <h1>Make every moment <em>count.</em></h1>
              <p className="welcome-copy">Turn long-form videos into short-form content worth watching.</p>
            </div>
            <button className="primary-button" onClick={() => setActive("Projects")}><Plus size={17} /> New project</button>
          </section>

          <section className="hero-panel">
            <div className="hero-content">
              <div className="hero-kicker"><WandSparkles size={14} /> YOUR NEXT CLIP STARTS HERE</div>
              <h2>One long video.<br /><span>Multiple standout moments.</span></h2>
              <p>Import a recording, let AI find the moments that matter, then shape each clip your way.</p>
              <button className="hero-button" onClick={() => setActive("Projects")}>Start a project <ArrowRight size={16} /></button>
            </div>
            <div className="hero-art" aria-hidden="true">
              <div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" />
              <div className="art-video"><div className="video-top"><span /><span /><span /></div><div className="waveform">{Array.from({ length: 34 }, (_, i) => <i key={i} style={{ height: `${18 + ((i * 17 + 9) % 62)}%` }} />)}</div><div className="video-time">00:42 <span> / 01:18:24</span></div></div>
              <div className="floating-chip chip-ai"><Sparkles size={13} /> AI moments</div>
              <div className="floating-chip chip-cut"><Zap size={13} /> Smart cuts</div>
            </div>
            <div className="hero-index">01 <span>—</span> 04</div>
          </section>

          <section className="section-heading"><div><h3>Workspace at a glance</h3><p>Your production overview</p></div><button className="text-button" onClick={() => setActive("Projects")}>View projects <ArrowRight size={14} /></button></section>
          <section className="stats-grid">
            <Stat icon={<Video size={17} />} label="Total projects" value="—" note="Ready when you are" />
            <Stat icon={<Clapperboard size={17} />} label="Clips created" value="—" note="Your highlights library" />
            <Stat icon={<Clock3 size={17} />} label="Video processed" value="—" note="Across all projects" />
            <Stat icon={<Activity size={17} />} label="Export queue" value="0" note="No active exports" />
          </section>
          <section className="bottom-grid">
            <div className="empty-panel">
              <div className="panel-heading"><h3>Recent projects</h3><span className="muted-label">LATEST ACTIVITY</span></div>
              <div className="empty-state"><div className="empty-icon"><Archive size={21} /></div><strong>No projects yet</strong><p>Your projects will show up here after you import a video.</p><button className="subtle-button" onClick={() => setActive("Projects")}>Create first project <ArrowRight size={14} /></button></div>
            </div>
            <div className="pipeline-panel">
              <div className="panel-heading"><h3>Workflow</h3><span className="muted-label">4 SIMPLE STEPS</span></div>
              {[
                ["01", "Import video", "Bring in a local video file"],
                ["02", "Find moments", "AI discovers clip candidates"],
                ["03", "Fine-tune", "Edit framing, cuts and captions"],
                ["04", "Export clips", "Render for Shorts and Reels"]
              ].map(([number, title, desc]) => <div className="pipeline-step" key={number}><span className="step-number">{number}</span><div><strong>{title}</strong><small>{desc}</small></div><ArrowRight size={14} /></div>)}
            </div>
          </section>
          <footer><span>PERSONAL CLIPPER STUDIO <b>•</b> FOUNDATION BUILD</span><span>LOCAL-FIRST BY DESIGN</span></footer>
        </div>
      </main>
    </div>
  );
}

function Stat({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) {
  return <div className="stat-card"><div className="stat-top"><span className="stat-icon">{icon}</span><span>{label}</span></div><strong className="stat-value">{value}</strong><small>{note}</small></div>;
}

export default App;
