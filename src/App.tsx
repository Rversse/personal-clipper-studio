import { useCallback, useEffect, useState } from "react";
import {
  Activity, Archive, ArrowDownToLine, ArrowRight, AudioLines, Clapperboard,
  Clock3, FolderOpen, Gauge, Layers3, Plus, Settings2, Sparkles, Upload,
  Video, WandSparkles, X, FileVideo, RefreshCw, Zap
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8765";

type Health = "checking" | "online" | "offline";
type Project = { id: string; name: string; description: string; created_at: string; updated_at: string; media_count: number };
type Media = { id: string; project_id: string; original_name: string; mime_type: string; file_size: number; duration: number | null; width: number | null; height: number | null; fps: number | null; video_codec: string | null; audio_codec: string | null; thumbnail_url: string | null; preview_url: string; created_at: string };
type Transcription = { id: string; media_id: string; status: string; model_name: string; language: string | null; detected_language: string | null; duration: number | null; progress: number; error: string | null; segments?: {start:number;end:number;text:string}[] };
type Discovery = { id: string; media_id: string; status: string; progress: number; error: string | null; candidates: {id:string;start_seconds:number;end_seconds:number;title:string;hook:string;summary:string;rationale:string;score:number;category:string}[] };

const navItems = [
  { label: "Overview", icon: Gauge },
  { label: "Projects", icon: FolderOpen },
  { label: "AI Workspace", icon: Sparkles },
  { label: "Clip Editor", icon: Clapperboard },
  { label: "Subtitle Studio", icon: AudioLines },
  { label: "Export Center", icon: ArrowDownToLine }
];

function formatBytes(bytes: number) {
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}
function formatDuration(seconds: number | null) {
  if (seconds === null) return "Durasi belum terbaca";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  return h ? `${h}j ${m}m ${s}d` : `${m}m ${s}d`;
}

function App() {
  const [active, setActive] = useState("Overview");
  const [health, setHealth] = useState<Health>("checking");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [media, setMedia] = useState<Media[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [aiProjectId, setAiProjectId] = useState("");
  const [aiMediaId, setAiMediaId] = useState("");
  const [whisperModel, setWhisperModel] = useState("small");
  const [whisperLanguage, setWhisperLanguage] = useState("id");
  const [transcription, setTranscription] = useState<Transcription | null>(null);
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const [discoveryMode, setDiscoveryMode] = useState("auto");
  const [discoveryCategory, setDiscoveryCategory] = useState("podcast");
  const [minDuration, setMinDuration] = useState(30);
  const [maxDuration, setMaxDuration] = useState(60);
  const [aiBusy, setAiBusy] = useState(false);

  const loadProjects = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/projects`);
      if (!response.ok) throw new Error("Gagal mengambil daftar proyek.");
      setProjects(await response.json() as Project[]);
      setHealth("online");
    } catch {
      setHealth("offline");
    }
  }, []);

  const loadMedia = useCallback(async (project: Project) => {
    setLoading(true); setError("");
    try {
      const response = await fetch(`${API_BASE}/api/projects/${project.id}/media`);
      if (!response.ok) throw new Error("Gagal mengambil media proyek.");
      setMedia(await response.json() as Media[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Terjadi kesalahan.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (active !== "AI Workspace" || !aiProjectId) return;
    const project = projects.find(item => item.id === aiProjectId);
    if (project) void loadMedia(project);
  }, [active, aiProjectId, projects, loadMedia]);

  useEffect(() => {
    if (!transcription || !["queued", "processing"].includes(transcription.status)) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/transcriptions/${transcription.id}/status`);
        if (!response.ok) throw new Error("Gagal memeriksa status transkripsi.");
        const status = await response.json() as Transcription;
        if (cancelled) return;
        setTranscription(previous => previous ? { ...previous, ...status } : status);
        if (status.status === "completed") {
          const detail = await fetch(`${API_BASE}/api/transcriptions/${transcription.id}`);
          if (detail.ok && !cancelled) setTranscription(await detail.json() as Transcription);
        } else if (status.status === "queued" || status.status === "processing") {
          timer = window.setTimeout(poll, 1800);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [transcription?.id, transcription?.status]);

  useEffect(() => {
    if (!discovery || !["queued", "processing"].includes(discovery.status)) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/discovery/${discovery.id}`);
        if (!response.ok) throw new Error("Gagal memeriksa status analisis AI.");
        const result = await response.json() as Discovery;
        if (cancelled) return;
        setDiscovery(result);
        if (result.status === "queued" || result.status === "processing") {
          timer = window.setTimeout(poll, 2200);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [discovery?.id, discovery?.status]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/api/health`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(() => { setHealth("online"); void loadProjects(); })
      .catch(() => setHealth("offline"));
    return () => controller.abort();
  }, [loadProjects]);

  async function createProject(event: React.FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return;
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/projects`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: projectName.trim(), description: projectDescription.trim() })
      });
      if (!response.ok) throw new Error((await response.json()).detail ?? "Gagal membuat proyek.");
      const project = await response.json() as Project;
      setProjectName(""); setProjectDescription(""); setModalOpen(false);
      await loadProjects(); setSelected(project); setActive("Projects"); await loadMedia(project);
    } catch (e) { setError(e instanceof Error ? e.message : "Terjadi kesalahan."); }
  }

  async function importVideo(file?: File) {
    if (!file || !selected) return;
    setUploading(true); setUploadProgress(`Mengimpor ${file.name}...`); setError("");
    const form = new FormData(); form.append("file", file);
    try {
      const response = await fetch(`${API_BASE}/api/projects/${selected.id}/media`, { method: "POST", body: form });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail ?? "Import video gagal.");
      }
      setUploadProgress("Video berhasil diimpor.");
      await loadMedia(selected); await loadProjects();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import video gagal.");
      setUploadProgress("");
    } finally { setUploading(false); }
  }

  async function startTranscription() {
    if (!aiMediaId) return;
    setAiBusy(true); setError(""); setDiscovery(null); setTranscription(null);
    try {
      const response = await fetch(`${API_BASE}/api/media/${aiMediaId}/transcription`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_name: whisperModel, language: whisperLanguage || null })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Gagal memulai transkripsi.");
      setTranscription(body as Transcription);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memulai transkripsi.");
    } finally { setAiBusy(false); }
  }

  async function startDiscovery() {
    if (!aiMediaId || !transcription || transcription.status !== "completed") return;
    setAiBusy(true); setError(""); setDiscovery(null);
    try {
      const response = await fetch(`${API_BASE}/api/media/${aiMediaId}/discovery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcription_id: transcription.id, mode: discoveryMode,
          category: discoveryMode === "category" ? discoveryCategory : null,
          min_duration: minDuration, max_duration: maxDuration
        })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Gagal memulai AI discovery.");
      setDiscovery(body as Discovery);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memulai AI discovery.");
    } finally { setAiBusy(false); }
  }

  function openProject(project: Project) {
    setSelected(project); setActive("Projects"); void loadMedia(project);
  }

  function formatTimestamp(seconds: number) {
    const total = Math.max(0, Math.floor(seconds));
    return `${String(Math.floor(total / 3600)).padStart(2, "0")}:${String(Math.floor((total % 3600) / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Clapperboard size={19} /></div><div><strong>clipper<span>.</span></strong><small>PERSONAL STUDIO</small></div></div>
        <div className="workspace-label">WORKSPACE</div>
        <nav>{navItems.map(({ label, icon: Icon }) => <button key={label} className={active === label ? "nav-item active" : "nav-item"} onClick={() => setActive(label)}><Icon size={17} strokeWidth={1.8} /><span>{label}</span>{label === "AI Workspace" && <span className="nav-tag">AI</span>}</button>)}</nav>
        <div className="sidebar-bottom">
          <div className="storage-card"><div className="storage-title"><Layers3 size={15} /> Local workspace</div><p>Project dan video tersimpan di perangkat ini.</p><div className="storage-line"><span /></div><small>Penggunaan storage akan tersedia nanti</small></div>
          <button className="nav-item settings" onClick={() => setActive("Settings")}><Settings2 size={17} /><span>Settings</span></button>
          <div className="profile-row"><div className="avatar">P</div><div><strong>Personal workspace</strong><small>Local device</small></div><span className="profile-dots">•••</span></div>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{active}</strong></div><div className="topbar-right"><div className={`service-status ${health}`}><i />{health === "online" ? "Local service connected" : health === "checking" ? "Connecting local service" : "Local service offline"}</div><div className="top-avatar">P</div></div></header>
        <div className="content">
          {active === "Projects" ? (
            <section className="projects-view">
              <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> PROJECT LIBRARY</div><h1>{selected ? selected.name : "Your projects."}</h1><p className="welcome-copy">{selected ? "Kelola video sumber dan aset proyek ini." : "Semua video dan pekerjaan clipping tersimpan di sini."}</p></div><div className="heading-actions">{selected && <button className="secondary-button" onClick={() => { setSelected(null); setMedia([]); }}>All projects</button>}<button className="primary-button" onClick={() => setModalOpen(true)}><Plus size={17} /> New project</button></div></div>
              {error && <div className="error-banner">{error}</div>}
              {!selected ? (
                projects.length ? <div className="project-grid">{projects.map(project => <button className="project-card" key={project.id} onClick={() => openProject(project)}><div className="project-card-icon"><FolderOpen size={20}/></div><div className="project-card-copy"><strong>{project.name}</strong><small>{project.description || "No description"}</small></div><div className="project-card-meta"><span><Video size={13}/> {project.media_count} video</span><ArrowRight size={15}/></div></button>)}</div> :
                <div className="empty-panel project-empty"><div className="empty-icon"><Archive size={21}/></div><strong>Belum ada proyek</strong><p>Buat proyek pertama untuk mulai mengelola video panjang dan hasil clipping.</p><button className="subtle-button" onClick={() => setModalOpen(true)}><Plus size={14}/> Buat proyek pertama</button></div>
              ) : (
                <div className="project-detail">
                  <div className="import-panel"><div className="import-icon"><Upload size={21}/></div><div className="import-copy"><strong>Import video source</strong><p>MP4, MOV, MKV, WEBM, AVI, M4V. File tetap disimpan lokal.</p>{uploadProgress && <small>{uploadProgress}</small>}</div><label className={uploading ? "upload-button disabled" : "upload-button"}><Upload size={15}/> {uploading ? "Mengimpor..." : "Choose video"}<input type="file" accept="video/*,.mkv,.avi,.m4v" disabled={uploading} onChange={e => { void importVideo(e.target.files?.[0]); e.currentTarget.value = ""; }}/></label></div>
                  <div className="media-heading"><div><h3>Source media</h3><p>{media.length} video dalam proyek ini</p></div><button className="icon-button" onClick={() => void loadMedia(selected)} title="Refresh"><RefreshCw size={15}/></button></div>
                  {loading ? <div className="loading-state">Memuat media...</div> : media.length ? <div className="media-grid">{media.map(item => <article className="media-card" key={item.id}><div className="media-thumb">{item.thumbnail_url ? <img src={API_BASE + item.thumbnail_url} alt="Video thumbnail"/> : <FileVideo size={28}/>}<span>{formatDuration(item.duration)}</span></div><div className="media-info"><strong title={item.original_name}>{item.original_name}</strong><small>{formatBytes(item.file_size)} · {item.width && item.height ? `${item.width}×${item.height}` : "Resolusi belum terbaca"}{item.fps ? ` · ${item.fps} FPS` : ""}</small><small>{item.video_codec ?? "Codec belum terdeteksi"}{item.audio_codec ? ` / ${item.audio_codec}` : ""}</small></div><video className="media-preview" controls preload="none" src={API_BASE + item.preview_url}/></article>)}</div> : <div className="empty-panel media-empty"><div className="empty-icon"><Video size={21}/></div><strong>Belum ada video source</strong><p>Import file video untuk mulai membangun project clipping.</p></div>}
                </div>
              )}
            </section>
          ) : (
            <>
              <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line" /> YOUR VIDEO WORKSPACE</div><h1>Make every moment <em>count.</em></h1><p className="welcome-copy">Turn long-form videos into short-form content worth watching.</p></div><button className="primary-button" onClick={() => { setActive("Projects"); setSelected(null); setModalOpen(true); }}><Plus size={17}/> New project</button></section>
              <section className="hero-panel"><div className="hero-content"><div className="hero-kicker"><WandSparkles size={14}/> YOUR NEXT CLIP STARTS HERE</div><h2>One long video.<br/><span>Multiple standout moments.</span></h2><p>Import a recording, let AI find the moments that matter, then shape each clip your way.</p><button className="hero-button" onClick={() => setActive("Projects")}>Explore projects <ArrowRight size={16}/></button></div><div className="hero-art" aria-hidden="true"><div className="art-orbit orbit-one"/><div className="art-orbit orbit-two"/><div className="art-video"><div className="video-top"><span/><span/><span/></div><div className="waveform">{Array.from({length:34},(_,i)=><i key={i} style={{height:`${18+((i*17+9)%62)}%`}}/>)}</div><div className="video-time">00:42 <span>/ 01:18:24</span></div></div><div className="floating-chip chip-ai"><Sparkles size={13}/> AI moments</div><div className="floating-chip chip-cut"><Zap size={13}/> Smart cuts</div></div><div className="hero-index">01 <span>—</span> 04</div></section>
              <section className="section-heading"><div><h3>Workspace at a glance</h3><p>Your production overview</p></div><button className="text-button" onClick={() => setActive("Projects")}>View projects <ArrowRight size={14}/></button></section>
              <section className="stats-grid"><Stat icon={<Video size={17}/>} label="Total projects" value={String(projects.length)} note="Saved in local workspace"/><Stat icon={<Clapperboard size={17}/>} label="Source videos" value={String(projects.reduce((n,p)=>n+p.media_count,0))} note="Across all projects"/><Stat icon={<Clock3 size={17}/>} label="Video processed" value="—" note="Duration totals coming later"/><Stat icon={<Activity size={17}/>} label="Export queue" value="0" note="No active exports"/></section>
              <section className="bottom-grid"><div className="empty-panel"><div className="panel-heading"><h3>Recent projects</h3><span className="muted-label">LATEST ACTIVITY</span></div>{projects.length ? <div className="recent-list">{projects.slice(0,4).map(p=><button key={p.id} onClick={()=>openProject(p)}><FolderOpen size={16}/><span><strong>{p.name}</strong><small>{p.media_count} video source</small></span><ArrowRight size={14}/></button>)}</div> : <div className="empty-state"><div className="empty-icon"><Archive size={21}/></div><strong>No projects yet</strong><p>Your projects will show up here after you import a video.</p><button className="subtle-button" onClick={()=>{setActive("Projects");setModalOpen(true);}}>Create first project <ArrowRight size={14}/></button></div>}</div><div className="pipeline-panel"><div className="panel-heading"><h3>Workflow</h3><span className="muted-label">4 SIMPLE STEPS</span></div>{[["01","Import video","Bring in a local video file"],["02","Find moments","AI discovers clip candidates"],["03","Fine-tune","Edit framing, cuts and captions"],["04","Export clips","Render for Shorts and Reels"]].map(([n,t,d])=><div className="pipeline-step" key={n}><span className="step-number">{n}</span><div><strong>{t}</strong><small>{d}</small></div><ArrowRight size={14}/></div>)}</div></section>
            </>
          )}
          <footer><span>PERSONAL CLIPPER STUDIO <b>•</b> PHASE 2 BUILD</span><span>LOCAL-FIRST BY DESIGN</span></footer>
        </div>
      </main>
      {modalOpen && <div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setModalOpen(false);}}><form className="project-modal" onSubmit={createProject}><div className="modal-heading"><div><span className="eyebrow">NEW WORKSPACE</span><h2>Create project</h2></div><button type="button" className="icon-button" onClick={()=>setModalOpen(false)}><X size={17}/></button></div><label>Project name<input autoFocus maxLength={120} value={projectName} onChange={e=>setProjectName(e.target.value)} placeholder="Contoh: Podcast Episode 01" required/></label><label>Description <span className="optional-label">OPTIONAL</span><textarea maxLength={2000} rows={3} value={projectDescription} onChange={e=>setProjectDescription(e.target.value)} placeholder="Catatan singkat tentang video atau project ini..."/></label><div className="modal-actions"><button type="button" className="secondary-button" onClick={()=>setModalOpen(false)}>Cancel</button><button className="primary-button" type="submit"><Plus size={15}/> Create project</button></div></form></div>}
    </div>
  );
}

function Stat({icon,label,value,note}:{icon:React.ReactNode;label:string;value:string;note:string}) {
  return <div className="stat-card"><div className="stat-top"><span className="stat-icon">{icon}</span><span>{label}</span></div><strong className="stat-value">{value}</strong><small>{note}</small></div>;
}
export default App;
