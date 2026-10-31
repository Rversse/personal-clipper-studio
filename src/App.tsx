import { useCallback, useEffect, useRef, useState } from "react";
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
  const [editorCandidateId, setEditorCandidateId] = useState("");
  const [editStart, setEditStart] = useState(0);
  const [editEnd, setEditEnd] = useState(0);
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [subtitleText, setSubtitleText] = useState<Record<number, string>>({});
  const [subtitlePreset, setSubtitlePreset] = useState("clean");
  const [splitPoints, setSplitPoints] = useState<number[]>([]);
  const [editorSaved, setEditorSaved] = useState(false);
  const previewRef = useRef<HTMLVideoElement>(null);
  const editorCandidate = editorCandidateId ? discovery?.candidates.find(item => item.id === editorCandidateId) : undefined;
  const editorDuration = Math.max(0, editEnd - editStart);
  const visibleSubtitleSegments = (transcription?.segments ?? []).filter(segment => segment.end > editStart && segment.start < editEnd);

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
    if (!aiMediaId) return;
    let cancelled = false;
    const restoreLatestJobs = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/media/${aiMediaId}/transcriptions/latest`);
        if (response.ok) {
          const status = await response.json() as Transcription;
          if (status.status === "completed") {
            const detail = await fetch(`${API_BASE}/api/transcriptions/${status.id}`);
            if (detail.ok && !cancelled) setTranscription(await detail.json() as Transcription);
            else if (!cancelled) setTranscription(status);
          } else if (!cancelled) {
            setTranscription(status);
          }
        }
      } catch { /* No previous transcription is a normal first-run state. */ }
      try {
        const response = await fetch(`${API_BASE}/api/media/${aiMediaId}/discovery/latest`);
        if (response.ok && !cancelled) setDiscovery(await response.json() as Discovery);
      } catch { /* No previous discovery is a normal first-run state. */ }
    };
    void restoreLatestJobs();
    return () => { cancelled = true; };
  }, [aiMediaId]);

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
        if (status.status === "completed") {
          const detail = await fetch(`${API_BASE}/api/transcriptions/${transcription.id}`);
          if (cancelled) return;
          setTranscription(detail.ok ? await detail.json() as Transcription : status);
        } else {
          setTranscription(previous => previous ? { ...previous, ...status } : status);
          if (status.status === "queued" || status.status === "processing") {
            timer = window.setTimeout(poll, 1800);
          }
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

  useEffect(() => {
    if (!aiMediaId) return;
    try {
      const saved = localStorage.getItem("pcs-editor-" + aiMediaId);
      if (saved) {
        const parsed = JSON.parse(saved) as { start?: number; end?: number; ratio?: string; texts?: Record<number,string>; preset?: string; splits?: number[] };
        setEditStart(parsed.start ?? 0);
        setEditEnd(parsed.end ?? (media.find(item => item.id === aiMediaId)?.duration ?? 0));
        setAspectRatio(parsed.ratio ?? "9:16");
        setSubtitleText(parsed.texts ?? {});
        setSubtitlePreset(parsed.preset ?? "clean");
        setSplitPoints(parsed.splits ?? []);
      } else {
        setEditStart(0);
        setEditEnd(media.find(item => item.id === aiMediaId)?.duration ?? 0);
        setSubtitleText({});
        setSplitPoints([]);
        setAspectRatio("9:16");
        setSubtitlePreset("clean");
      }
    } catch {
      setEditStart(0);
      setEditEnd(media.find(item => item.id === aiMediaId)?.duration ?? 0);
      setSubtitleText({});
      setSplitPoints([]);
    }
    setEditorCandidateId("");
    setEditorSaved(false);
  }, [aiMediaId, media]);

  useEffect(() => {
    if (editEnd > 0 || !transcription?.segments?.length) return;
    setEditEnd(transcription.duration ?? transcription.segments[transcription.segments.length - 1].end);
  }, [transcription?.id, transcription?.duration, transcription?.segments?.length, editEnd]);

  useEffect(() => {
    if (!editorCandidate) return;
    setEditStart(editorCandidate.start_seconds);
    setEditEnd(editorCandidate.end_seconds);
  }, [editorCandidate?.id]);

  function saveEditorDraft() {
    if (!aiMediaId || editEnd <= editStart) return;
    localStorage.setItem("pcs-editor-" + aiMediaId, JSON.stringify({
      start: editStart, end: editEnd, ratio: aspectRatio, texts: subtitleText, preset: subtitlePreset, splits: splitPoints
    }));
    setEditorSaved(true);
    window.setTimeout(() => setEditorSaved(false), 2400);
  }

  function addSplitPoint() {
    const point = previewRef.current?.currentTime;
    if (point === undefined || point <= editStart || point >= editEnd) return;
    setSplitPoints(previous => [...previous, point].sort((a,b)=>a-b).filter((value,index,array)=>index===0 || value-array[index-1]>0.25));
  }

  function seekPreview(seconds: number) {
    if (previewRef.current) {
      previewRef.current.currentTime = seconds;
      void previewRef.current.play();
    }
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
          {active === "AI Workspace" ? (
            <section className="ai-workspace">
              <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> TRANSCRIPTION & DISCOVERY</div><h1>AI Workspace.</h1><p className="welcome-copy">Transkrip video secara lokal, lalu temukan momen terbaik untuk Shorts dan Reels.</p></div></div>
              {error && <div className="error-banner">{error}</div>}
              <div className="ai-setup-grid">
                <label>Project<select value={aiProjectId} onChange={e=>{setAiProjectId(e.target.value);setAiMediaId("");setTranscription(null);setDiscovery(null);}}><option value="">Pilih project</option>{projects.map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
                <label>Source video<select value={aiMediaId} onChange={e=>{setAiMediaId(e.target.value);setTranscription(null);setDiscovery(null);setError("");}} disabled={!aiProjectId || loading}><option value="">Pilih video</option>{media.map(item=><option key={item.id} value={item.id}>{item.original_name}</option>)}</select></label>
              </div>
              {!projects.length ? <div className="empty-panel ai-empty"><strong>Belum ada project</strong><p>Buat project dan import video terlebih dahulu.</p><button className="subtle-button" onClick={()=>{setActive("Projects");setModalOpen(true);}}><Plus size={14}/> Buat project</button></div> :
              !aiProjectId ? <div className="empty-panel ai-empty"><strong>Pilih project untuk mulai</strong><p>Pilih project yang berisi video sumber.</p></div> :
              loading ? <div className="loading-state">Memuat video project...</div> :
              !media.length ? <div className="empty-panel ai-empty"><strong>Belum ada video di project ini</strong><p>Import video dari halaman Projects, lalu kembali ke AI Workspace.</p><button className="subtle-button" onClick={()=>setActive("Projects")}>Buka Projects <ArrowRight size={14}/></button></div> :
              !aiMediaId ? <div className="empty-panel ai-empty"><strong>Pilih video sumber</strong><p>Pilih video yang ingin ditranskrip dan dianalisis.</p></div> :
              <>
                <div className="ai-panel">
                  <div className="ai-panel-heading"><div><span className="ai-step">01</span><div><h3>Local transcription</h3><p>Whisper memproses audio di perangkat ini.</p></div></div><span className="ai-local-tag">LOCAL PROCESSING</span></div>
                  <div className="ai-control-grid">
                    <label>Whisper model<select value={whisperModel} onChange={e=>setWhisperModel(e.target.value)}><option value="tiny">Tiny · paling cepat</option><option value="base">Base · ringan</option><option value="small">Small · balanced</option><option value="medium">Medium · lebih akurat</option><option value="large-v3">Large v3 · paling berat</option><option value="distil-large-v3">Distil Large v3</option></select></label>
                    <label>Bahasa audio<select value={whisperLanguage} onChange={e=>setWhisperLanguage(e.target.value)}><option value="id">Indonesia</option><option value="en">English</option><option value="">Auto detect</option></select></label>
                    <button className="primary-button ai-action" onClick={()=>void startTranscription()} disabled={aiBusy || !!transcription && ["queued","processing"].includes(transcription.status)}><AudioLines size={15}/>{transcription?.status==="processing" || transcription?.status==="queued" ? "Transcribing..." : "Start transcription"}</button>
                  </div>
                  {transcription && <div className="job-status"><div className="job-status-top"><strong>{transcription.status==="completed" ? "Transcription completed" : transcription.status==="failed" ? "Transcription failed" : "Transcription in progress"}</strong><span>{Math.round((transcription.progress ?? 0)*100)}%</span></div><div className="progress-track"><i style={{width:`${Math.max(0,Math.min(100,(transcription.progress ?? 0)*100))}%`}}/></div>{transcription.status==="completed" && <small>{transcription.detected_language?.toUpperCase() ?? "Language detected"} · {transcription.segments?.length ?? 0} transcript segments</small>}{transcription.status==="failed" && <small className="job-error">{transcription.error ?? "Proses transkripsi gagal."}</small>}</div>}
                </div>
                {transcription?.status==="completed" && <div className="ai-panel">
                  <div className="ai-panel-heading"><div><span className="ai-step">02</span><div><h3>AI Clip Discovery</h3><p>Analisis transkrip dan temukan momen yang layak dipotong.</p></div></div><span className="ai-local-tag">TRANSCRIPT ONLY</span></div>
                  <div className="ai-control-grid discovery-controls">
                    <label>Analysis mode<select value={discoveryMode} onChange={e=>setDiscoveryMode(e.target.value)}><option value="auto">Auto Discovery</option><option value="category">Category Analysis</option></select></label>
                    {discoveryMode==="category" && <label>Category<select value={discoveryCategory} onChange={e=>setDiscoveryCategory(e.target.value)}><option value="podcast">Podcast</option><option value="gaming">Gaming</option><option value="education">Education</option><option value="motivation">Motivation</option><option value="comedy">Comedy</option><option value="news">News</option><option value="storytelling">Storytelling</option><option value="custom">Custom</option></select></label>}
                    <label>Minimum duration<select value={minDuration} onChange={e=>setMinDuration(Number(e.target.value))}><option value={15}>15 seconds</option><option value={30}>30 seconds</option><option value={45}>45 seconds</option><option value={60}>60 seconds</option><option value={90}>90 seconds</option></select></label>
                    <label>Maximum duration<select value={maxDuration} onChange={e=>setMaxDuration(Number(e.target.value))}><option value={30}>30 seconds</option><option value={60}>60 seconds</option><option value={90}>90 seconds</option><option value={120}>120 seconds</option><option value={180}>180 seconds</option></select></label>
                    <button className="primary-button ai-action" onClick={()=>void startDiscovery()} disabled={aiBusy || minDuration>maxDuration || !!discovery && ["queued","processing"].includes(discovery.status)}><Sparkles size={15}/>{discovery?.status==="processing" || discovery?.status==="queued" ? "Analyzing..." : "Find clip moments"}</button>
                  </div>
                  {minDuration>maxDuration && <small className="job-error">Durasi minimum tidak boleh melebihi durasi maksimum.</small>}
                  {discovery && <div className="job-status"><div className="job-status-top"><strong>{discovery.status==="completed" ? "Analysis completed" : discovery.status==="failed" ? "Analysis failed" : "Analyzing transcript"}</strong><span>{Math.round(discovery.progress*100)}%</span></div><div className="progress-track"><i style={{width:`${Math.max(0,Math.min(100,discovery.progress*100))}%`}}/></div>{discovery.status==="failed" && <small className="job-error">{discovery.error ?? "Analisis AI gagal."}</small>}</div>}
                  {discovery?.status==="completed" && <div className="candidate-section"><div className="media-heading"><div><h3>Clip candidates</h3><p>{discovery.candidates.length} kandidat ditemukan · urut berdasarkan skor</p></div></div>{discovery.candidates.length ? <div className="candidate-list">{discovery.candidates.map(candidate=><article className="candidate-card" key={candidate.id}><div className="candidate-top"><span>{formatTimestamp(candidate.start_seconds)} — {formatTimestamp(candidate.end_seconds)}</span><strong>{candidate.score}<small>/100</small></strong></div><h4>{candidate.title}</h4><p className="candidate-hook">{candidate.hook}</p><p>{candidate.summary}</p><small>{candidate.rationale}</small><div className="candidate-meta"><span>{candidate.category}</span><span>{Math.round(candidate.end_seconds-candidate.start_seconds)} sec</span></div></article>)}</div> : <div className="empty-panel ai-empty"><strong>Belum ada kandidat yang memenuhi kriteria</strong><p>Coba mode analisis lain atau rentang durasi berbeda.</p></div>}</div>}
                </div>}
                <p className="ai-provider-note">Provider AI dikonfigurasi di backend. Gemini mengirim teks transkrip saja dengan penyimpanan interaksi dinonaktifkan; Ollama dapat berjalan sepenuhnya lokal.</p>
              </>}
            </section>
          ) : active === "Clip Editor" ? (
            <section className="editor-view">
              <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> CLIP EDITING</div><h1>Clip Editor.</h1><p className="welcome-copy">Pilih kandidat AI, rapikan batas klip, dan tentukan format video.</p></div></div>
              {!aiMediaId || !media.some(item => item.id === aiMediaId) ? <div className="empty-panel ai-empty"><strong>Pilih video dari AI Workspace</strong><p>Pilih project dan source video di AI Workspace terlebih dahulu.</p><button className="subtle-button" onClick={()=>setActive("AI Workspace")}>Buka AI Workspace <ArrowRight size={14}/></button></div> :
              <div className="editor-layout">
                <div className="editor-preview-panel">
                  <div className="editor-panel-head"><div><strong>Preview monitor</strong><small>{media.find(item=>item.id===aiMediaId)?.original_name}</small></div><span className="editor-live-tag">SOURCE PREVIEW</span></div>
                  <div className={"preview-stage ratio-" + aspectRatio.replace(":","-")}><video ref={previewRef} controls preload="metadata" src={API_BASE + (media.find(item=>item.id===aiMediaId)?.preview_url ?? "")} onLoadedMetadata={e=>{if(editEnd<=0&&Number.isFinite(e.currentTarget.duration))setEditEnd(e.currentTarget.duration);if(editStart>0)e.currentTarget.currentTime=editStart;}} /></div>
                  <div className="preview-actions"><button className="secondary-button" onClick={()=>seekPreview(editStart)}>Play from in</button><button className="secondary-button" onClick={()=>seekPreview(editEnd)}>Go to out</button></div>
                  <div className="timeline-panel"><div className="timeline-heading"><strong>Clip range</strong><span>{formatDuration(editorDuration)}</span></div>
                    <div className="timeline-track"><i style={{left:((editStart/(media.find(item=>item.id===aiMediaId)?.duration || 1))*100)+"%",width:((editorDuration/(media.find(item=>item.id===aiMediaId)?.duration || 1))*100)+"%"}}/>{splitPoints.filter(point=>point>editStart&&point<editEnd).map(point=><b key={point} className="timeline-split" style={{left:(((point-editStart)/editorDuration)*100)+"%"}}/>)}</div>
                    <div className="split-toolbar"><button className="secondary-button" onClick={addSplitPoint}>Split at playhead</button><span>{splitPoints.filter(point=>point>editStart&&point<editEnd).length} split point</span></div>
                    {splitPoints.filter(point=>point>editStart&&point<editEnd).length>0 && <div className="split-list">{splitPoints.filter(point=>point>editStart&&point<editEnd).map(point=><button key={point} onClick={()=>setSplitPoints(previous=>previous.filter(item=>item!==point))}>{formatTimestamp(point)} <X size={11}/></button>)}</div>}
                    <div className="trim-grid">
                      <label>In point (seconds)<input type="number" min="0" step="0.1" value={editStart} onChange={e=>setEditStart(Math.max(0,Math.min(Number(e.target.value),editEnd-0.1)))}/></label>
                      <label>Out point (seconds)<input type="number" min={editStart+0.1} step="0.1" value={editEnd} onChange={e=>setEditEnd(Math.max(editStart+0.1,Number(e.target.value)))}/></label>
                    </div>
                  </div>
                </div>
                <aside className="editor-settings">
                  <div className="editor-panel-head"><div><strong>Clip settings</strong><small>Basic edit controls</small></div></div>
                  <label>AI candidate<select value={editorCandidateId} onChange={e=>{setEditorCandidateId(e.target.value);if(!e.target.value){setEditStart(0);setEditEnd(media.find(item=>item.id===aiMediaId)?.duration ?? transcription?.duration ?? 0);}}}><option value="">Manual / full source</option>{(discovery?.candidates ?? []).map(c=><option key={c.id} value={c.id}>{c.title} · {c.score}/100</option>)}</select></label>
                  <label>Aspect ratio<select value={aspectRatio} onChange={e=>setAspectRatio(e.target.value)}><option value="9:16">9:16 · Shorts / Reels</option><option value="16:9">16:9 · Landscape</option><option value="1:1">1:1 · Square</option></select></label>
                  <div className="editor-info"><span>Selected duration</span><strong>{formatDuration(editorDuration)}</strong></div>
                  <div className="editor-info"><span>Subtitle segments</span><strong>{visibleSubtitleSegments.length}</strong></div>
                  <button className="primary-button editor-save" onClick={saveEditorDraft} disabled={editEnd<=editStart}>Save editing draft</button>
                  {editorSaved && <small className="save-confirmation">Draft tersimpan di perangkat ini.</small>}
                  <p className="editor-disclaimer">Pengaturan edit tersimpan lokal. Rendering dan crop/reposition masuk ke Phase 5.</p>
                </aside>
              </div>}
            </section>
          ) : active === "Subtitle Studio" ? (
            <section className="subtitle-view">
              <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> CAPTION DESIGN</div><h1>Subtitle Studio.</h1><p className="welcome-copy">Koreksi hasil Whisper dan atur gaya subtitle sebelum rendering.</p></div></div>
              {!aiMediaId || !transcription?.segments?.length ? <div className="empty-panel ai-empty"><strong>Belum ada transkrip yang siap diedit</strong><p>Pilih video dan selesaikan transkripsi di AI Workspace terlebih dahulu.</p><button className="subtle-button" onClick={()=>setActive("AI Workspace")}>Buka AI Workspace <ArrowRight size={14}/></button></div> :
              <div className="subtitle-layout">
                <div className="subtitle-toolbar"><label>Subtitle preset<select value={subtitlePreset} onChange={e=>setSubtitlePreset(e.target.value)}><option value="clean">Clean · minimal</option><option value="bold">Bold · high contrast</option><option value="highlight">Highlight · creator style</option></select></label><span>{visibleSubtitleSegments.length} baris dalam rentang klip</span><button className="primary-button" onClick={saveEditorDraft}>Save subtitle edits</button></div>
                <div className={"subtitle-sample " + subtitlePreset}><span>{visibleSubtitleSegments.length ? (subtitleText[transcription.segments.indexOf(visibleSubtitleSegments[0])] ?? visibleSubtitleSegments[0].text) : "Preview subtitle"}</span></div>
                <div className="subtitle-list">{visibleSubtitleSegments.map(segment=>{const index=transcription.segments!.indexOf(segment);return <div className="subtitle-row" key={segment.start + "-" + index}><button className="subtitle-time" onClick={()=>seekPreview(segment.start)}>{formatTimestamp(segment.start)}</button><textarea rows={2} value={subtitleText[index] ?? segment.text} onChange={e=>setSubtitleText(previous=>({...previous,[index]:e.target.value}))} aria-label={"Subtitle at " + formatTimestamp(segment.start)}/></div>})}</div>
                <p className="editor-disclaimer">Perubahan subtitle dan preset disimpan sebagai draft lokal. Timing kata dan burn-in dilakukan pada tahap rendering.</p>
              </div>}
            </section>
          ) : active === "Projects" ? (
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
          <footer><span>PERSONAL CLIPPER STUDIO <b>•</b> PHASE 4 BUILD</span><span>LOCAL-FIRST BY DESIGN</span></footer>
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
