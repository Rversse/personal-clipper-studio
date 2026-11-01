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
type TranscriptWord = { start:number; end:number; text:string; probability:number|null };
type TranscriptSegment = { id:number|null; start:number; end:number; text:string; words:TranscriptWord[] };
type Transcription = { id: string; media_id: string; status: string; model_name: string; language: string | null; detected_language: string | null; duration: number | null; progress: number; error: string | null; segments?: TranscriptSegment[] };
type ClipSegment = { start: number; end: number; crop_zoom?: number; crop_x?: number; crop_y?: number; audio_volume?: number };
type ExportJob = { id:string; media_id:string; status:string; start_seconds:number; end_seconds:number; aspect_ratio:string; output_name:string; clip_count:number; output_preset?:string; quality_profile?:string; frame_rate?:number|null; normalize_audio?:boolean; encoder:string|null; progress:number; error:string|null; download_url:string|null; created_at:string; updated_at:string };
type Discovery = { id: string; media_id: string; status: string; progress: number; error: string | null; candidates: {id:string;start_seconds:number;end_seconds:number;title:string;hook:string;summary:string;rationale:string;score:number;category:string;boundary_quality:number;opening_boundary:string;ending_boundary:string;context_note:string}[] };
type RuntimeDependency = { id:string; name:string; required:boolean; available:boolean; status:string; description:string; download_url:string|null; download_label:string|null; install_note:string };
type RuntimeDiagnostics = { platform:string; ffmpeg:{available:boolean;version:string|null}; ffprobe:{available:boolean;version:string|null}; dependencies:RuntimeDependency[]; transcription:{installed:boolean;device:string;compute_type:string;model_downloaded:boolean|null}; ai:{provider:string;model:string|null;configured:boolean;status:string} };

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
  const [runtimeDiagnostics, setRuntimeDiagnostics] = useState<RuntimeDiagnostics | null>(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [diagnosticsError, setDiagnosticsError] = useState("");
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
  const [outputPreset, setOutputPreset] = useState("youtube_shorts");
  const [qualityProfile, setQualityProfile] = useState("balanced");
  const [frameRate, setFrameRate] = useState<number | null>(30);
  const [normalizeAudio, setNormalizeAudio] = useState(false);
  const [cropZoom, setCropZoom] = useState(1);
  const [cropX, setCropX] = useState(0.5);
  const [cropY, setCropY] = useState(0.5);
  const [audioVolume, setAudioVolume] = useState(1);
  const [subtitleText, setSubtitleText] = useState<Record<number, string>>({});
  const [subtitleTiming, setSubtitleTiming] = useState<Record<number, { start: number; end: number }>>({});
  const [wordTiming, setWordTiming] = useState<Record<string, {start:number;end:number;text:string}>>({});
  const [wordBusy, setWordBusy] = useState<string>("");
  const wordSaveLockRef = useRef(false);
  const [subtitlePreset, setSubtitlePreset] = useState("clean");
  const [subtitleFontSize, setSubtitleFontSize] = useState(52);
  const [subtitlePosition, setSubtitlePosition] = useState(8);
  const [subtitleOutline, setSubtitleOutline] = useState(2);
  const [subtitleBackground, setSubtitleBackground] = useState(0);
  const [burnSubtitles, setBurnSubtitles] = useState(false);
  const [splitPoints, setSplitPoints] = useState<number[]>([]);
  const [clipSequence, setClipSequence] = useState<ClipSegment[]>([]);
  const [selectedSequenceIndex, setSelectedSequenceIndex] = useState<number | null>(null);
  const [draggedTimelineIndex, setDraggedTimelineIndex] = useState<number | null>(null);
  const timelineLaneRef = useRef<HTMLDivElement>(null);
  const timelineResizeRef = useRef<{
    index: number; edge: "start" | "end"; startX: number;
    initialStart: number; initialEnd: number; secondsPerPixel: number;
    isSequence: boolean;
  } | null>(null);
  const captionTimingDragRef = useRef<{
    index: number; mode: "move" | "start" | "end"; startX: number;
    initialStart: number; initialEnd: number; secondsPerPixel: number;
  } | null>(null);
  const wordTimingDragRef = useRef<{
    segment: TranscriptSegment; wordIndex: number;
    mode: "move" | "start" | "end"; startX: number;
    initialStart: number; initialEnd: number; secondsPerPixel: number;
    minStart: number; maxEnd: number;
  } | null>(null);
  const [playheadSeconds, setPlayheadSeconds] = useState(0);
  const [editorSaved, setEditorSaved] = useState(false);
  const [exportJobs, setExportJobs] = useState<ExportJob[]>([]);
  const [exportBusy, setExportBusy] = useState(false);
  const previewRef = useRef<HTMLVideoElement>(null);
  const editorCandidate = editorCandidateId ? discovery?.candidates.find(item => item.id === editorCandidateId) : undefined;
  const editorDuration = Math.max(0, editEnd - editStart);
  const editorSourceDuration = Math.max(0, media.find(item => item.id === aiMediaId)?.duration ?? previewRef.current?.duration ?? 0);
  const visibleSplitPoints = splitPoints.filter(point => point > editStart && point < editEnd);
  const timelineTranscriptSegments = transcription?.media_id === aiMediaId ? transcription.segments ?? [] : [];
  function getSubtitleRange(index: number, segment: { start: number; end: number }) {
    return subtitleTiming[index] ?? { start: segment.start, end: segment.end };
  }
  const visibleSubtitleSegments = timelineTranscriptSegments.filter(segment => {
    const index = timelineTranscriptSegments.indexOf(segment);
    const timing = getSubtitleRange(index, segment);
    return timing.end > editStart && timing.start < editEnd;
  });
  const timelineClips: ClipSegment[] = clipSequence.length ? clipSequence : editEnd > editStart
    ? [{ start: editStart, end: editEnd, crop_zoom: cropZoom, crop_x: cropX, crop_y: cropY }]
    : [];
  const timelineTotalDuration = Math.max(0.001, timelineClips.reduce((sum, clip) => sum + clip.end - clip.start, 0));
  const timelineSelectedIndex = selectedSequenceIndex !== null && selectedSequenceIndex < timelineClips.length
    ? selectedSequenceIndex : timelineClips.length ? 0 : null;
  const timelinePlayheadOutput = (() => {
    if (!timelineClips.length) return 0;
    const sourcePosition = (index: number) => timelineClips.slice(0,index).reduce((sum,clip)=>sum+clip.end-clip.start,0);
    if (timelineSelectedIndex !== null) {
      const selected = timelineClips[timelineSelectedIndex];
      if (playheadSeconds >= selected.start && playheadSeconds <= selected.end) {
        return sourcePosition(timelineSelectedIndex) + playheadSeconds - selected.start;
      }
    }
    let elapsed = 0;
    for (const clip of timelineClips) {
      if (playheadSeconds >= clip.start && playheadSeconds <= clip.end) {
        return elapsed + playheadSeconds - clip.start;
      }
      elapsed += clip.end - clip.start;
    }
    const index = timelineSelectedIndex ?? 0;
    const selected = timelineClips[index];
    return selected ? sourcePosition(index) + Math.max(0,Math.min(selected.end-selected.start,playheadSeconds-selected.start)) : 0;
  })();

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

  const loadRuntimeDiagnostics = useCallback(async () => {
    setDiagnosticsLoading(true);
    setDiagnosticsError("");
    try {
      const response = await fetch(`${API_BASE}/api/diagnostics`);
      if (!response.ok) throw new Error("Tidak dapat membaca pemeriksaan software dari local service.");
      setRuntimeDiagnostics(await response.json() as RuntimeDiagnostics);
    } catch (e) {
      setDiagnosticsError(e instanceof Error ? e.message : "Pemeriksaan software gagal.");
    } finally {
      setDiagnosticsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (active === "Settings") void loadRuntimeDiagnostics();
  }, [active, loadRuntimeDiagnostics]);

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
    setTranscription(null);
    setSubtitleText({});
    setSubtitleTiming({});
    setWordTiming({});
    setBurnSubtitles(false);
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

  const loadExports = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/exports`);
      if (response.ok) setExportJobs(await response.json() as ExportJob[]);
    } catch { /* Export history remains available when the local API reconnects. */ }
  }, []);

  useEffect(() => {
    if (active !== "Export Center") return;
    void loadExports();
    const timer = window.setInterval(() => void loadExports(), 2500);
    return () => window.clearInterval(timer);
  }, [active, loadExports]);

  async function startExport() {
    if (!aiMediaId || editEnd <= editStart) return;
    const selectedClips = clipSequence.length ? clipSequence : [{ start: editStart, end: editEnd, audio_volume: audioVolume }];
    const subtitleSource = transcription?.media_id === aiMediaId ? transcription.segments ?? [] : [];
    const selectedSubtitles = subtitleSource.flatMap((segment,index) => {
      const timing = getSubtitleRange(index, segment);
      const words = segment.words?.length
        ? segment.words.map((word,wordIndex)=>currentWordTiming(segment.id,wordIndex,word))
        : [];
      const correctedText = subtitleText[index];
      if (words.length && hasCompleteWordTiming(segment) && (correctedText === undefined || correctedText === segment.text)) {
        return words
          .map(word=>({start:Math.max(word.start,timing.start),end:Math.min(word.end,timing.end),text:word.text}))
          .filter(word=>word.end>word.start && selectedClips.some(clip=>word.end>clip.start&&word.start<clip.end));
      }
      return selectedClips.some(clip=>timing.end>clip.start&&timing.start<clip.end)
        ? [{start:timing.start,end:timing.end,text:correctedText ?? segment.text}]
        : [];
    });
    setExportBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/media/${aiMediaId}/exports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_seconds: Math.min(...selectedClips.map(clip => clip.start)),
          end_seconds: Math.max(...selectedClips.map(clip => clip.end)),
          clip_segments: selectedClips.map(clip => ({
            ...clip,
            crop_zoom: clip.crop_zoom ?? cropZoom,
            crop_x: clip.crop_x ?? cropX,
            crop_y: clip.crop_y ?? cropY,
            audio_volume: clip.audio_volume ?? audioVolume
          })),
          aspect_ratio: aspectRatio,
          output_preset: outputPreset, quality_profile: qualityProfile, frame_rate: frameRate, normalize_audio: normalizeAudio,
          crop_zoom: cropZoom, crop_x: cropX, crop_y: cropY, audio_volume: audioVolume,
          burn_subtitles: burnSubtitles,
          subtitle_preset: subtitlePreset,
          subtitle_style: { font_size: subtitleFontSize, vertical_position: subtitlePosition, outline_width: subtitleOutline, background_opacity: subtitleBackground },
          subtitle_segments: burnSubtitles ? selectedSubtitles : []
        })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Gagal memulai export.");
      setExportJobs(previous => [body as ExportJob, ...previous.filter(job => job.id !== body.id)]);
      setActive("Export Center");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memulai export.");
    } finally {
      setExportBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    let connected = false;

    const checkHealth = async () => {
      try {
        const response = await fetch(`${API_BASE}/api/health`, { signal: controller.signal });
        if (!response.ok) throw new Error("Local service health check failed.");
        if (!connected) void loadProjects();
        connected = true;
        setHealth("online");
      } catch {
        if (controller.signal.aborted) return;
        connected = false;
        setHealth("offline");
      }

      if (!controller.signal.aborted) {
        timer = window.setTimeout(checkHealth, connected ? 5000 : 1000);
      }
    };

    void checkHealth();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
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
    setClipSequence([]);
    setSelectedSequenceIndex(null);
    setAudioVolume(1);
    setSubtitleTiming({});
    setCropZoom(1);
    setCropX(0.5);
    setCropY(0.5);
    try {
      const saved = localStorage.getItem("pcs-editor-" + aiMediaId);
      if (saved) {
        const parsed = JSON.parse(saved) as { start?: number; end?: number; ratio?: string; outputPreset?: string; qualityProfile?: string; frameRate?: number | null; normalizeAudio?: boolean; texts?: Record<number,string>; subtitleTiming?: Record<number,{start:number;end:number}>; preset?: string; splits?: number[]; sequence?: ClipSegment[]; selectedSequenceIndex?: number | null; cropZoom?: number; cropX?: number; cropY?: number; audioVolume?: number; subtitleFontSize?: number; subtitlePosition?: number; subtitleOutline?: number; subtitleBackground?: number };
        setEditStart(parsed.start ?? 0);
        setEditEnd(parsed.end ?? (media.find(item => item.id === aiMediaId)?.duration ?? 0));
        setAspectRatio(parsed.ratio ?? "9:16");
        const savedOutputPreset = ["youtube_shorts","instagram_reels","facebook_reels","square_feed","landscape","custom"].includes(parsed.outputPreset ?? "")
          ? parsed.outputPreset!
          : parsed.ratio === "1:1" ? "square_feed" : parsed.ratio === "16:9" ? "landscape" : "youtube_shorts";
        setOutputPreset(savedOutputPreset);
        setQualityProfile(["balanced","high","maximum"].includes(parsed.qualityProfile ?? "") ? parsed.qualityProfile! : "balanced");
        setFrameRate(parsed.frameRate === null ? null : [24,30,60].includes(parsed.frameRate ?? 30) ? parsed.frameRate ?? 30 : 30);
        setNormalizeAudio(parsed.normalizeAudio ?? false);
        setSubtitleText(parsed.texts ?? {});
        setSubtitleTiming(parsed.subtitleTiming ?? {});
        setSubtitlePreset(parsed.preset ?? "clean");
        setSubtitleFontSize(Math.max(24, Math.min(120, parsed.subtitleFontSize ?? 52)));
        setSubtitlePosition(Math.max(5, Math.min(30, parsed.subtitlePosition ?? 8)));
        setSubtitleOutline(Math.max(0, Math.min(8, parsed.subtitleOutline ?? 2)));
        setSubtitleBackground(Math.max(0, Math.min(90, parsed.subtitleBackground ?? 0)));
        setSplitPoints(parsed.splits ?? []);
        const restoredSequence = Array.isArray(parsed.sequence)
          ? parsed.sequence.filter(item => Number.isFinite(item.start) && Number.isFinite(item.end) && item.start >= 0 && item.end > item.start)
            .map(item => ({ ...item, audio_volume: Number.isFinite(item.audio_volume) ? Math.max(0, Math.min(2, item.audio_volume!)) : 1 }))
          : [];
        setClipSequence(restoredSequence);
        const restoredIndex = Number.isInteger(parsed.selectedSequenceIndex) && parsed.selectedSequenceIndex! >= 0 && parsed.selectedSequenceIndex! < restoredSequence.length ? parsed.selectedSequenceIndex! : null;
        setSelectedSequenceIndex(restoredIndex);
        setAudioVolume(restoredIndex !== null ? restoredSequence[restoredIndex].audio_volume ?? 1 : Math.max(0, Math.min(2, parsed.audioVolume ?? 1)));
        setCropZoom(Math.max(1, Math.min(3, parsed.cropZoom ?? 1)));
        setCropX(Math.max(0, Math.min(1, parsed.cropX ?? 0.5)));
        setCropY(Math.max(0, Math.min(1, parsed.cropY ?? 0.5)));
      } else {
        setEditStart(0);
        setEditEnd(media.find(item => item.id === aiMediaId)?.duration ?? 0);
        setSubtitleText({});
        setSubtitleTiming({});
        setAudioVolume(1);
        setSplitPoints([]);
        setClipSequence([]);
        setAspectRatio("9:16");
        setOutputPreset("youtube_shorts"); setQualityProfile("balanced"); setFrameRate(30); setNormalizeAudio(false);
        setCropZoom(1); setCropX(0.5); setCropY(0.5);
        setSubtitlePreset("clean");
        setSubtitleFontSize(52); setSubtitlePosition(8); setSubtitleOutline(2); setSubtitleBackground(0);
      }
    } catch {
      setEditStart(0);
      setEditEnd(media.find(item => item.id === aiMediaId)?.duration ?? 0);
      setSubtitleText({});
      setSubtitleTiming({});
      setAudioVolume(1);
      setOutputPreset("youtube_shorts"); setQualityProfile("balanced"); setFrameRate(30); setNormalizeAudio(false);
      setSubtitleFontSize(52); setSubtitlePosition(8); setSubtitleOutline(2); setSubtitleBackground(0);
      setSplitPoints([]);
      setClipSequence([]);
      setCropZoom(1); setCropX(0.5); setCropY(0.5);
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
      start: editStart, end: editEnd, ratio: aspectRatio, outputPreset, qualityProfile, frameRate, normalizeAudio, texts: subtitleText, subtitleTiming, preset: subtitlePreset, splits: splitPoints, sequence: clipSequence, selectedSequenceIndex, cropZoom, cropX, cropY, audioVolume, subtitleFontSize, subtitlePosition, subtitleOutline, subtitleBackground
    }));
    setEditorSaved(true);
    window.setTimeout(() => setEditorSaved(false), 2400);
  }

  function addSplitPoint() {
    const point = previewRef.current?.currentTime;
    if (point === undefined || point <= editStart || point >= editEnd) return;
    setSplitPoints(previous => [...previous, point].sort((a,b)=>a-b).filter((value,index,array)=>index===0 || value-array[index-1]>0.25));
  }

  function updateCropFraming(values: Partial<Pick<ClipSegment, "crop_zoom" | "crop_x" | "crop_y">>) {
    const nextZoom = values.crop_zoom ?? cropZoom;
    const nextX = values.crop_x ?? cropX;
    const nextY = values.crop_y ?? cropY;
    if (values.crop_zoom !== undefined) setCropZoom(values.crop_zoom);
    if (values.crop_x !== undefined) setCropX(values.crop_x);
    if (values.crop_y !== undefined) setCropY(values.crop_y);
    if (selectedSequenceIndex !== null) {
      setClipSequence(previous => previous.map((clip, index) => index === selectedSequenceIndex
        ? { ...clip, crop_zoom: nextZoom, crop_x: nextX, crop_y: nextY }
        : clip));
    }
  }

  function addCurrentRangeToSequence() {
    if (editEnd <= editStart || clipSequence.length >= 100) return;
    const nextIndex = clipSequence.length;
    setClipSequence(previous => [...previous, {
      start: editStart, end: editEnd, crop_zoom: cropZoom, crop_x: cropX, crop_y: cropY, audio_volume: audioVolume
    }]);
    setSelectedSequenceIndex(nextIndex);
  }

  function addSplitRangesToSequence() {
    const points = splitPoints.filter(point => point > editStart && point < editEnd).sort((a,b)=>a-b);
    if (!points.length) return;
    const boundaries = [editStart, ...points, editEnd];
    const ranges = boundaries.slice(0, -1).map((start, index) => ({
      start, end: boundaries[index + 1], crop_zoom: cropZoom, crop_x: cropX, crop_y: cropY, audio_volume: audioVolume
    }));
    const nextIndex = clipSequence.length;
    setClipSequence(previous => [...previous, ...ranges].slice(0, 100));
    setSelectedSequenceIndex(nextIndex);
  }

  function moveSequenceClip(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= clipSequence.length) return;
    setClipSequence(previous => {
      const next = [...previous];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    if (selectedSequenceIndex === index) setSelectedSequenceIndex(target);
    else if (selectedSequenceIndex === target) setSelectedSequenceIndex(index);
  }

  function removeSequenceClip(index: number) {
    setClipSequence(previous => previous.filter((_, itemIndex) => itemIndex !== index));
    if (selectedSequenceIndex === index) setSelectedSequenceIndex(null);
    else if (selectedSequenceIndex !== null && selectedSequenceIndex > index) {
      setSelectedSequenceIndex(selectedSequenceIndex - 1);
    }
  }

  function reorderTimelineClip(fromIndex: number, toIndex: number) {
    if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= clipSequence.length || toIndex >= clipSequence.length) return;
    setClipSequence(previous => {
      const next = [...previous];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      return next;
    });
    setSelectedSequenceIndex(current => {
      if (current === null) return null;
      if (current === fromIndex) return toIndex;
      if (fromIndex < current && current <= toIndex) return current - 1;
      if (toIndex <= current && current < fromIndex) return current + 1;
      return current;
    });
  }

  function seekTimelineOutput(clientX: number, lane: HTMLDivElement) {
    if (!timelineClips.length) return;
    const rect = lane.getBoundingClientRect();
    if (rect.width <= 0) return;
    const outputTime = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * timelineTotalDuration;
    let elapsed = 0;
    for (let index = 0; index < timelineClips.length; index += 1) {
      const clip = timelineClips[index];
      const clipDuration = clip.end - clip.start;
      if (outputTime <= elapsed + clipDuration || index === timelineClips.length - 1) {
        const sourceTime = clip.start + Math.max(0, Math.min(clipDuration, outputTime - elapsed));
        if (clipSequence.length) selectSequenceClip(clip, index, false);
        seekPreview(sourceTime);
        return;
      }
      elapsed += clipDuration;
    }
  }

  function updateSelectedAudioVolume(value: number) {
    const next = Math.max(0, Math.min(2, Number.isFinite(value) ? value : 1));
    setAudioVolume(next);
    if (selectedSequenceIndex !== null) {
      setClipSequence(previous => previous.map((clip,index)=>index===selectedSequenceIndex
        ? { ...clip, audio_volume: next }
        : clip));
    } else if (editEnd > editStart) {
      setClipSequence([{ start: editStart, end: editEnd, crop_zoom: cropZoom, crop_x: cropX, crop_y: cropY, audio_volume: next }]);
      setSelectedSequenceIndex(0);
    }
  }

  function wordKey(segmentId:number|null, wordIndex:number) { return `${segmentId ?? "legacy"}:${wordIndex}`; }
  function currentWordTiming(segmentId:number|null, wordIndex:number, word:TranscriptWord) {
    return wordTiming[wordKey(segmentId,wordIndex)] ?? {start:word.start,end:word.end,text:word.text};
  }
  function hasCompleteWordTiming(segment:TranscriptSegment) {
    if (!segment.words?.length) return false;
    const tokens = (value:string) => value.normalize("NFKC")
      .match(/[\p{L}\p{M}\p{N}_]+|[^\p{L}\p{M}\p{N}\s]/gu)
      ?.map(token => token.toLocaleLowerCase("id")) ?? [];
    const expected = tokens(segment.text);
    const actual = tokens(segment.words.map(word => word.text.trim()).join(" "));
    return expected.length > 0 && expected.length === actual.length
      && expected.every((token,index)=>token === actual[index]);
  }
  async function saveWordTiming(segment:TranscriptSegment, wordIndex:number, next:{start:number;end:number;text:string}) {
    if (!segment.id || !transcription || wordSaveLockRef.current) return;
    wordSaveLockRef.current = true;
    const requestTranscriptionId = transcription.id;
    const key=wordKey(segment.id,wordIndex);
    setWordBusy(key);
    try {
      const response=await fetch(`${API_BASE}/api/transcriptions/${requestTranscriptionId}/segments/${segment.id}/words/${wordIndex}`,{
        method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(next)
      });
      const result=await response.json();
      if(!response.ok) throw new Error(result.detail ?? "Gagal menyimpan timing kata.");
      setTranscription(previous => previous?.id === requestTranscriptionId ? result as Transcription : previous);
      setWordTiming(previous=>{const copy={...previous};delete copy[key];return copy;});
    } catch(error) {
      // Keep the edited value so the user can retry after a transient API error.
      setWordTiming(previous=>({...previous,[key]:next}));
      setError(error instanceof Error ? error.message : "Gagal menyimpan timing kata.");
    } finally {
      wordSaveLockRef.current = false;
      setWordBusy("");
    }
  }
  function getWordDragRange(drag: NonNullable<typeof wordTimingDragRef.current>, clientX: number) {
    const delta = (clientX - drag.startX) * drag.secondsPerPixel;
    const duration = drag.initialEnd - drag.initialStart;
    let start = drag.initialStart;
    let end = drag.initialEnd;
    if (drag.mode === "move") {
      start = Math.max(drag.minStart, Math.min(drag.maxEnd - duration, drag.initialStart + delta));
      end = start + duration;
    } else if (drag.mode === "start") {
      start = Math.max(drag.minStart, Math.min(drag.initialEnd - 0.01, drag.initialStart + delta));
    } else {
      end = Math.min(drag.maxEnd, Math.max(drag.initialStart + 0.01, drag.initialEnd + delta));
    }
    return { start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) };
  }

  function beginWordTiming(
    event: React.PointerEvent<HTMLElement>,
    segment: TranscriptSegment,
    wordIndex: number,
    mode: "move" | "start" | "end",
  ) {
    if (wordBusy || wordSaveLockRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    const lane = event.currentTarget.closest(".word-timing-lane") as HTMLDivElement | null;
    const captureTarget = event.currentTarget.closest(".timeline-word-clip") as HTMLElement | null;
    const word = segment.words?.[wordIndex];
    if (!lane || !captureTarget || !word) return;
    const timing = currentWordTiming(segment.id, wordIndex, word);
    const previousWord = wordIndex > 0 ? segment.words[wordIndex - 1] : undefined;
    const nextWord = wordIndex < segment.words.length - 1 ? segment.words[wordIndex + 1] : undefined;
    const previousTiming = previousWord ? currentWordTiming(segment.id, wordIndex - 1, previousWord) : undefined;
    const nextTiming = nextWord ? currentWordTiming(segment.id, wordIndex + 1, nextWord) : undefined;
    captureTarget.setPointerCapture(event.pointerId);
    wordTimingDragRef.current = {
      segment, wordIndex, mode, startX: event.clientX,
      initialStart: timing.start, initialEnd: timing.end,
      secondsPerPixel: timelineTotalDuration / Math.max(1, lane.getBoundingClientRect().width),
      minStart: previousTiming?.end ?? segment.start,
      maxEnd: nextTiming?.start ?? segment.end,
    };
  }

  function moveWordTiming(event: React.PointerEvent<HTMLElement>) {
    const drag = wordTimingDragRef.current;
    if (!drag) return;
    const range = getWordDragRange(drag, event.clientX);
    const key = wordKey(drag.segment.id, drag.wordIndex);
    const current = currentWordTiming(drag.segment.id, drag.wordIndex, drag.segment.words[drag.wordIndex]);
    setWordTiming(previous => ({ ...previous, [key]: { ...current, ...range } }));
  }

  function finishWordTiming(event: React.PointerEvent<HTMLElement>) {
    const drag = wordTimingDragRef.current;
    if (!drag) return;
    const range = getWordDragRange(drag, event.clientX);
    const word = drag.segment.words[drag.wordIndex];
    const current = currentWordTiming(drag.segment.id, drag.wordIndex, word);
    const next = { ...current, ...range };
    const key = wordKey(drag.segment.id, drag.wordIndex);
    wordTimingDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setWordTiming(previous => ({ ...previous, [key]: next }));
    void saveWordTiming(drag.segment, drag.wordIndex, next);
  }

  function cancelWordTiming(event: React.PointerEvent<HTMLElement>) {
    const drag = wordTimingDragRef.current;
    if (drag) {
      const key = wordKey(drag.segment.id, drag.wordIndex);
      setWordTiming(previous => { const copy = { ...previous }; delete copy[key]; return copy; });
    }
    wordTimingDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function updateSubtitleRange(index: number, edge: "start" | "end", value: number) {
    const segment = timelineTranscriptSegments[index];
    if (!segment || !Number.isFinite(value)) return;
    const current = getSubtitleRange(index, segment);
    const maximum = editorSourceDuration > 0 ? editorSourceDuration : Number.MAX_SAFE_INTEGER;
    const nextStart = edge === "start" ? Math.max(0, Math.min(value, current.end - 0.05)) : current.start;
    const nextEnd = edge === "end" ? Math.min(maximum, Math.max(current.start + 0.05, value)) : current.end;
    setSubtitleTiming(previous => ({ ...previous, [index]: { start: nextStart, end: nextEnd } }));
  }

  function beginCaptionTiming(event: React.PointerEvent<HTMLElement>, index: number, mode: "move" | "start" | "end") {
    event.preventDefault();
    event.stopPropagation();
    const segment = timelineTranscriptSegments[index];
    const lane = event.currentTarget.closest(".captions-lane") as HTMLDivElement | null;
    if (!segment || !lane) return;
    const timing = getSubtitleRange(index, segment);
    event.currentTarget.setPointerCapture(event.pointerId);
    captionTimingDragRef.current = {
      index, mode, startX: event.clientX,
      initialStart: timing.start, initialEnd: timing.end,
      secondsPerPixel: timelineTotalDuration / Math.max(1, lane.getBoundingClientRect().width),
    };
  }

  function moveCaptionTiming(event: React.PointerEvent<HTMLElement>) {
    const drag = captionTimingDragRef.current;
    if (!drag) return;
    const delta = (event.clientX - drag.startX) * drag.secondsPerPixel;
    const duration = drag.initialEnd - drag.initialStart;
    const maximum = editorSourceDuration > 0 ? editorSourceDuration : Number.MAX_SAFE_INTEGER;
    let start = drag.initialStart;
    let end = drag.initialEnd;
    if (drag.mode === "move") {
      start = Math.max(0, Math.min(maximum - duration, drag.initialStart + delta));
      end = start + duration;
    } else if (drag.mode === "start") {
      start = Math.max(0, Math.min(drag.initialEnd - 0.05, drag.initialStart + delta));
    } else {
      end = Math.min(maximum, Math.max(drag.initialStart + 0.05, drag.initialEnd + delta));
    }
    setSubtitleTiming(previous => ({
      ...previous,
      [drag.index]: { start: Number(start.toFixed(3)), end: Number(end.toFixed(3)) }
    }));
  }

  function endCaptionTiming(event: React.PointerEvent<HTMLElement>) {
    captionTimingDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function updateEditorTrim(edge: "start" | "end", value: number) {
    const activeStart = selectedSequenceIndex !== null ? clipSequence[selectedSequenceIndex]?.start ?? editStart : editStart;
    const activeEnd = selectedSequenceIndex !== null ? clipSequence[selectedSequenceIndex]?.end ?? editEnd : editEnd;
    const maximum = editorSourceDuration > 0 ? editorSourceDuration : Number.MAX_SAFE_INTEGER;
    const nextStart = edge === "start" ? Math.max(0, Math.min(value, activeEnd - 0.5)) : activeStart;
    const nextEnd = edge === "end" ? Math.min(maximum, Math.max(activeStart + 0.5, value)) : activeEnd;
    setEditStart(nextStart);
    setEditEnd(nextEnd);
    if (selectedSequenceIndex !== null) {
      setClipSequence(previous => previous.map((clip,index)=>index===selectedSequenceIndex
        ? { ...clip, start: nextStart, end: nextEnd }
        : clip));
    }
  }

  function splitSelectedTimelineClip() {
    const index = selectedSequenceIndex;
    const clip = index !== null ? clipSequence[index] : { start: editStart, end: editEnd, crop_zoom: cropZoom, crop_x: cropX, crop_y: cropY };
    if (!clip) return;
    const point = previewRef.current?.currentTime ?? playheadSeconds;
    if (point <= clip.start + 0.1 || point >= clip.end - 0.1) return;
    const before = { ...clip, end: point };
    const after = { ...clip, start: point };
    if (index === null) {
      setClipSequence([before, after]);
      setSelectedSequenceIndex(1);
    } else {
      setClipSequence(previous => previous.flatMap((item, itemIndex) => itemIndex === index ? [before, after] : [item]));
      setSelectedSequenceIndex(index + 1);
    }
    setEditStart(point);
    setEditEnd(clip.end);
    seekPreview(point);
  }

  function beginTimelineTrim(event: React.PointerEvent<HTMLSpanElement>, index: number, edge: "start" | "end") {
    event.preventDefault();
    event.stopPropagation();
    const lane = timelineLaneRef.current;
    if (!lane || !timelineClips[index]) return;
    const clip = timelineClips[index];
    if (clipSequence.length) {
      setSelectedSequenceIndex(index);
      setEditStart(clip.start);
      setEditEnd(clip.end);
      setCropZoom(clip.crop_zoom ?? cropZoom);
      setCropX(clip.crop_x ?? cropX);
      setCropY(clip.crop_y ?? cropY);
    }
    const element = event.currentTarget;
    element.setPointerCapture(event.pointerId);
    timelineResizeRef.current = {
      index, edge, startX: event.clientX,
      initialStart: clip.start, initialEnd: clip.end,
      secondsPerPixel: timelineTotalDuration / Math.max(1, lane.getBoundingClientRect().width),
      isSequence: clipSequence.length > 0,
    };
  }

  function moveTimelineTrim(event: React.PointerEvent<HTMLSpanElement>) {
    const drag = timelineResizeRef.current;
    if (!drag) return;
    const delta = (event.clientX - drag.startX) * drag.secondsPerPixel;
    const nextStart = drag.edge === "start"
      ? Math.max(0, Math.min(drag.initialEnd - 0.5, drag.initialStart + delta))
      : drag.initialStart;
    const maximum = editorSourceDuration > 0 ? editorSourceDuration : Number.MAX_SAFE_INTEGER;
    const nextEnd = drag.edge === "end"
      ? Math.min(maximum, Math.max(drag.initialStart + 0.5, drag.initialEnd + delta))
      : drag.initialEnd;
    if (drag.isSequence) {
      setClipSequence(previous => previous.map((clip, index) => index === drag.index
        ? { ...clip, start: nextStart, end: nextEnd }
        : clip));
      if (selectedSequenceIndex === drag.index) {
        setEditStart(nextStart);
        setEditEnd(nextEnd);
      }
    } else {
      setEditStart(nextStart);
      setEditEnd(nextEnd);
    }
  }

  function endTimelineTrim(event: React.PointerEvent<HTMLSpanElement>) {
    timelineResizeRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function seekPreview(seconds: number) {
    if (previewRef.current) {
      previewRef.current.currentTime = seconds;
      setPlayheadSeconds(seconds);
      void previewRef.current.play();
    }
  }

  function selectSequenceClip(clip: ClipSegment, index: number, seek = true) {
    setSelectedSequenceIndex(index);
    setEditorCandidateId("");
    setEditStart(clip.start);
    setEditEnd(clip.end);
    setCropZoom(clip.crop_zoom ?? cropZoom);
    setCropX(clip.crop_x ?? cropX);
    setCropY(clip.crop_y ?? cropY);
    setAudioVolume(clip.audio_volume ?? 1);
    if (seek) seekPreview(clip.start);
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
                  {discovery?.status==="completed" && <div className="candidate-section"><div className="media-heading"><div><h3>Clip candidates</h3><p>{discovery.candidates.length} kandidat ditemukan · urut berdasarkan skor</p></div></div>{discovery.candidates.length ? <div className="candidate-list">{discovery.candidates.map(candidate=><article className="candidate-card" key={candidate.id}><div className="candidate-top"><span>{formatTimestamp(candidate.start_seconds)} — {formatTimestamp(candidate.end_seconds)}</span><strong>{candidate.score}<small>/100</small></strong></div><h4>{candidate.title}</h4><p className="candidate-hook">{candidate.hook}</p><p>{candidate.summary}</p><small>{candidate.rationale}</small><div className="candidate-boundary-quality"><span>Boundary quality</span><strong>{candidate.boundary_quality ?? 50}/100</strong></div><div className="candidate-boundary-notes"><span><b>OPEN</b> {candidate.opening_boundary ?? "Batas segmen transkrip"}</span><span><b>CLOSE</b> {candidate.ending_boundary ?? "Batas segmen transkrip"}</span></div><small className="candidate-context-note">{candidate.context_note ?? "Batas klip diselaraskan dengan segmen transkrip."}</small><div className="candidate-meta"><span>{candidate.category}</span><span>{Math.round(candidate.end_seconds-candidate.start_seconds)} sec</span></div></article>)}</div> : <div className="empty-panel ai-empty"><strong>Belum ada kandidat yang memenuhi kriteria</strong><p>Coba mode analisis lain atau rentang durasi berbeda.</p></div>}</div>}
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
                  <div className={"preview-stage ratio-" + aspectRatio.replace(":","-")}><video ref={previewRef} controls preload="metadata" style={{ objectFit: "cover", objectPosition: `${cropX*100}% ${cropY*100}%`, transform: `scale(${cropZoom})` }} src={API_BASE + (media.find(item=>item.id===aiMediaId)?.preview_url ?? "")} onTimeUpdate={e=>setPlayheadSeconds(e.currentTarget.currentTime)} onLoadedMetadata={e=>{if(editEnd<=0&&Number.isFinite(e.currentTarget.duration))setEditEnd(e.currentTarget.duration);if(editStart>0)e.currentTarget.currentTime=editStart;setPlayheadSeconds(e.currentTarget.currentTime);}} /></div>
                  <div className="preview-actions"><button className="secondary-button" onClick={()=>seekPreview(editStart)}>Play from in</button><button className="secondary-button" onClick={()=>seekPreview(editEnd)}>Go to out</button></div>
                  <div className="timeline-panel"><div className="timeline-heading"><strong>Source timeline</strong><span>{formatTimestamp(editStart)} – {formatTimestamp(editEnd)} · {formatDuration(editorDuration)}</span></div>
                    <div className="timeline-ruler">{[0, 0.25, 0.5, 0.75, 1].map(fraction=><span key={fraction} style={{left:(fraction*100)+"%"}}>{formatTimestamp(editorSourceDuration*fraction)}</span>)}</div>
                    <div className="timeline-track" role="img" aria-label={"Selected range "+formatTimestamp(editStart)+" sampai "+formatTimestamp(editEnd)}>
                      <i style={{left:(editorSourceDuration ? editStart/editorSourceDuration*100 : 0)+"%",width:(editorSourceDuration ? editorDuration/editorSourceDuration*100 : 0)+"%"}}/>
                      {visibleSplitPoints.map(point=><b key={point} className="timeline-split" style={{left:(editorSourceDuration ? point/editorSourceDuration*100 : 0)+"%"}}/>)}
                      <em className="timeline-playhead" style={{left:(editorSourceDuration ? Math.min(100,playheadSeconds/editorSourceDuration*100) : 0)+"%"}}/>
                    </div>
                    <div className="timeline-legend"><span><i/> Selected range</span><span><b/> Split point</span><span><em/> Playhead</span></div>
                    <div className="split-toolbar"><button className="secondary-button" onClick={addSplitPoint}>Split at playhead</button><span>{splitPoints.filter(point=>point>editStart&&point<editEnd).length} split point</span></div>
                    {splitPoints.some(point=>point>editStart&&point<editEnd) && <button className="secondary-button sequence-add" onClick={addSplitRangesToSequence} disabled={clipSequence.length>=100}>Add split ranges to sequence</button>}
                    {splitPoints.filter(point=>point>editStart&&point<editEnd).length>0 && <div className="split-list">{splitPoints.filter(point=>point>editStart&&point<editEnd).map(point=><button key={point} onClick={()=>setSplitPoints(previous=>previous.filter(item=>item!==point))}>{formatTimestamp(point)} <X size={11}/></button>)}</div>}
                    <section className="multitrack-panel" aria-label="Multi-track timeline editor">
                      <div className="multitrack-heading">
                        <div><strong>Timeline</strong><small>Arrange, trim, split, and review the export sequence</small></div>
                        <span>{timelineClips.length} clips · {formatDuration(timelineTotalDuration)}</span>
                      </div>
                      <div className="multitrack-ruler">
                        <div className="multitrack-label-spacer">TRACKS</div>
                        <div className="multitrack-ruler-lane">
                          {[0,0.25,0.5,0.75,1].map(fraction=><span key={fraction} style={{left:(fraction*100)+"%"}}>{formatTimestamp(timelineTotalDuration*fraction)}</span>)}
                        </div>
                      </div>
                      <div className="multitrack-row">
                        <div className="multitrack-label"><b>VIDEO</b><small>{clipSequence.length ? "Sequence" : "Active range"}</small></div>
                        <div className="multitrack-lane video-lane" ref={timelineLaneRef} onClick={event=>seekTimelineOutput(event.clientX,event.currentTarget)}>
                          {timelineClips.map((clip,index)=>{
                            const offset=timelineClips.slice(0,index).reduce((sum,item)=>sum+item.end-item.start,0);
                            const left=offset/timelineTotalDuration*100;
                            const width=(clip.end-clip.start)/timelineTotalDuration*100;
                            return <div key={"timeline-video-"+index}
                              className={"multitrack-clip video-clip"+(timelineSelectedIndex===index?" selected":"")+(draggedTimelineIndex===index?" dragging":"")}
                              style={{left:left+"%",width:width+"%"}}
                              draggable={clipSequence.length>1}
                              onDragStart={event=>{if((event.target as HTMLElement).closest(".timeline-trim-handle")){event.preventDefault();return;}event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/plain",String(index));setDraggedTimelineIndex(index);}}
                              onDragOver={event=>{if(clipSequence.length>1){event.preventDefault();event.dataTransfer.dropEffect="move";}}}
                              onDrop={event=>{event.preventDefault();const from=Number(event.dataTransfer.getData("text/plain"));if(Number.isInteger(from))reorderTimelineClip(from,index);setDraggedTimelineIndex(null);}}
                              onDragEnd={()=>setDraggedTimelineIndex(null)}
                              onClick={event=>{event.stopPropagation();if(clipSequence.length)selectSequenceClip(clip,index,false);seekPreview(clip.start);}}
                              onKeyDown={event=>{
                                if((event.key==="Enter"||event.key===" ")&&clipSequence.length){event.preventDefault();selectSequenceClip(clip,index);return;}
                                if(event.key==="ArrowLeft"&&clipSequence.length>1){event.preventDefault();reorderTimelineClip(index,Math.max(0,index-1));}
                                if(event.key==="ArrowRight"&&clipSequence.length>1){event.preventDefault();reorderTimelineClip(index,Math.min(clipSequence.length-1,index+1));}
                              }}
                              role="button" tabIndex={0} aria-label={"Video clip "+(index+1)+", "+formatTimestamp(clip.start)+" to "+formatTimestamp(clip.end)}>
                              {clipSequence.length>0 && <span className="timeline-trim-handle left" draggable={false} title="Drag to trim clip start" onClick={event=>event.stopPropagation()} onPointerDown={event=>beginTimelineTrim(event,index,"start")} onPointerMove={moveTimelineTrim} onPointerUp={endTimelineTrim} onPointerCancel={endTimelineTrim}/>}
                              <b>{String(index+1).padStart(2,"0")}</b><span className="timeline-clip-time">{formatTimestamp(clip.start)}–{formatTimestamp(clip.end)}</span>
                              <small>{(clip.crop_zoom??1).toFixed(1)}×</small>
                              {clipSequence.length>0 && <span className="timeline-trim-handle right" draggable={false} title="Drag to trim clip end" onClick={event=>event.stopPropagation()} onPointerDown={event=>beginTimelineTrim(event,index,"end")} onPointerMove={moveTimelineTrim} onPointerUp={endTimelineTrim} onPointerCancel={endTimelineTrim}/>}
                            </div>;
                          })}
                          {timelineClips.length>0 && <i className="multitrack-playhead" style={{left:(timelinePlayheadOutput/timelineTotalDuration*100)+"%"}}/>}
                        </div>
                      </div>
                      <div className="multitrack-row">
                        <div className="multitrack-label"><b>AUDIO</b><small>Per-clip gain</small></div>
                        <div className="multitrack-lane audio-lane" onClick={event=>seekTimelineOutput(event.clientX,event.currentTarget)}>
                          {timelineClips.map((clip,index)=>{
                            const offset=timelineClips.slice(0,index).reduce((sum,item)=>sum+item.end-item.start,0);
                            const volume=Math.round((clip.audio_volume ?? 1)*100);
                            return <div key={"audio-"+index} className={"multitrack-clip audio-clip"+(timelineSelectedIndex===index?" selected":"")} style={{left:(offset/timelineTotalDuration*100)+"%",width:((clip.end-clip.start)/timelineTotalDuration*100)+"%"}} onClick={event=>{event.stopPropagation();if(clipSequence.length)selectSequenceClip(clip,index,false);seekPreview(clip.start);}}>
                              <span className="audio-linked-label">{volume===0?"MUTED":"VOL "+volume+"%"}</span>
                            </div>;
                          })}
                        </div>
                      </div>
                      {timelineSelectedIndex !== null && <div className="timeline-audio-controls">
                        <strong>Audio · Clip {timelineSelectedIndex+1}</strong>
                        <button type="button" className="secondary-button audio-mute-button" onClick={()=>updateSelectedAudioVolume(audioVolume===0?1:0)}>{audioVolume===0?"Unmute":"Mute"}</button>
                        <label><span>Volume</span><input type="range" min="0" max="200" step="5" value={Math.round(audioVolume*100)} onChange={event=>updateSelectedAudioVolume(Number(event.target.value)/100)}/><output>{Math.round(audioVolume*100)}%</output></label>
                      </div>}
                      <div className="multitrack-row">
                        <div className="multitrack-label"><b>CAPTIONS</b><small>Transcript</small></div>
                        <div className="multitrack-lane captions-lane" onClick={event=>seekTimelineOutput(event.clientX,event.currentTarget)}>
                          {timelineClips.flatMap((clip,index)=>{
                            const offset=timelineClips.slice(0,index).reduce((sum,item)=>sum+item.end-item.start,0);
                            return timelineTranscriptSegments.flatMap((segment,sourceSegmentIndex)=>{
                              const timing=getSubtitleRange(sourceSegmentIndex,segment);
                              if(timing.end<=clip.start||timing.start>=clip.end) return [];
                              const start=Math.max(timing.start,clip.start);
                              const end=Math.min(timing.end,clip.end);
                              const captionText=subtitleText[sourceSegmentIndex] ?? segment.text;
                              const dragProps={
                                onPointerMove:moveCaptionTiming,
                                onPointerUp:endCaptionTiming,
                                onPointerCancel:endCaptionTiming,
                              };
                              return [<button type="button" key={"caption-"+index+"-"+sourceSegmentIndex}
                                className="timeline-caption-clip"
                                style={{left:((offset+start-clip.start)/timelineTotalDuration*100)+"%",width:((end-start)/timelineTotalDuration*100)+"%"}}
                                title={captionText}
                                onPointerDown={event=>beginCaptionTiming(event,sourceSegmentIndex,"move")}
                                {...dragProps}
                                onClick={event=>{event.stopPropagation();if(clipSequence.length)selectSequenceClip(clip,index,false);seekPreview(start);}}>
                                <span className="caption-trim-handle left" title="Trim caption start" onClick={event=>event.stopPropagation()} onPointerDown={event=>beginCaptionTiming(event,sourceSegmentIndex,"start")} {...dragProps}/>
                                <span className="caption-block-text">{captionText}</span>
                                <span className="caption-trim-handle right" title="Trim caption end" onClick={event=>event.stopPropagation()} onPointerDown={event=>beginCaptionTiming(event,sourceSegmentIndex,"end")} {...dragProps}/>
                              </button>];
                            });
                          })}
                          {timelineClips.length>0&&timelineTranscriptSegments.length===0&&<span className="timeline-empty-hint">Transcribe video to populate caption track</span>}
                        </div>
                      </div>
                      <div className="multitrack-row">
                        <div className="multitrack-label"><b>WORDS</b><small>Word timing · drag to adjust</small></div>
                        <div className="multitrack-lane word-timing-lane" onClick={event=>seekTimelineOutput(event.clientX,event.currentTarget)}>
                          {timelineClips.flatMap((clip,clipIndex)=>{
                            const offset=timelineClips.slice(0,clipIndex).reduce((sum,item)=>sum+item.end-item.start,0);
                            return timelineTranscriptSegments.flatMap((segment,segmentIndex)=>(segment.words ?? []).flatMap((word,wordIndex)=>{
                              const timing=currentWordTiming(segment.id,wordIndex,word);
                              if(timing.end<=clip.start || timing.start>=clip.end) return [];
                              const start=Math.max(timing.start,clip.start);
                              const end=Math.min(timing.end,clip.end);

                              const dragProps={
                                onPointerMove:moveWordTiming,
                                onPointerUp:finishWordTiming,
                                onPointerCancel:cancelWordTiming,
                              };
                              const isActive=playheadSeconds>=timing.start && playheadSeconds<=timing.end;
                              return [<button
                                type="button"
                                key={"word-"+clipIndex+"-"+segmentIndex+"-"+wordIndex}
                                className={"timeline-word-clip"+(isActive?" active":"")}
                                style={{
                                  left:((offset+start-clip.start)/timelineTotalDuration*100)+"%",
                                  width:((end-start)/timelineTotalDuration*100)+"%"
                                }}
                                title={word.text+" · "+formatTimestamp(timing.start)+"–"+formatTimestamp(timing.end)}
                                aria-label={"Word "+word.text+", "+formatTimestamp(timing.start)+" to "+formatTimestamp(timing.end)}
                                onPointerDown={event=>beginWordTiming(event,segment,wordIndex,"move")}
                                {...dragProps}
                                onClick={event=>{
                                  event.stopPropagation();
                                  if(clipSequence.length) selectSequenceClip(clip,clipIndex,false);
                                  seekPreview(timing.start);
                                }}>
                                <span className="word-trim-handle left" title="Trim word start" onClick={event=>event.stopPropagation()} onPointerDown={event=>beginWordTiming(event,segment,wordIndex,"start")}/>
                                <span className="word-clip-text">{timing.text}</span>
                                <span className="word-trim-handle right" title="Trim word end" onClick={event=>event.stopPropagation()} onPointerDown={event=>beginWordTiming(event,segment,wordIndex,"end")}/>
                              </button>];
                            }));
                          })}
                          {timelineClips.length>0&&!timelineTranscriptSegments.some(segment=>segment.words?.length)&&<span className="timeline-empty-hint">Word timestamps unavailable · segment caption fallback is active</span>}
                        </div>
                      </div>
                      <div className="multitrack-footer">
                        <span>Drag clips to reorder · trim captions or individual words for precise timing</span>
                        <button className="secondary-button" onClick={splitSelectedTimelineClip} disabled={!timelineClips.length || playheadSeconds<=((timelineSelectedIndex===null?editStart:timelineClips[timelineSelectedIndex].start)+0.1) || playheadSeconds>=((timelineSelectedIndex===null?editEnd:timelineClips[timelineSelectedIndex].end)-0.1)}>Split selected at playhead</button>
                      </div>
                    </section>
                    <div className="sequence-panel">
                      <div className="sequence-heading"><strong>Render sequence</strong><span>{clipSequence.length} clips · {formatDuration(clipSequence.reduce((sum,clip)=>sum+clip.end-clip.start,0))}</span></div>
                      <button className="secondary-button sequence-add" onClick={addCurrentRangeToSequence} disabled={editEnd<=editStart||clipSequence.length>=100}>Add current range to sequence <Plus size={13}/></button>
                      {clipSequence.length>0 ? <ol className="sequence-list">{clipSequence.map((clip,index)=><li key={index}><span><b>{String(index+1).padStart(2,"0")}</b>{formatTimestamp(clip.start)} – {formatTimestamp(clip.end)}<small>{formatDuration(clip.end-clip.start)} · {(clip.crop_zoom ?? 1).toFixed(1)}× zoom</small></span><div><button aria-label="Move clip up" title="Move up" disabled={index===0} onClick={()=>moveSequenceClip(index,-1)}>↑</button><button aria-label="Move clip down" title="Move down" disabled={index===clipSequence.length-1} onClick={()=>moveSequenceClip(index,1)}>↓</button><button aria-label="Remove clip" title="Remove" onClick={()=>removeSequenceClip(index)}><X size={12}/></button></div></li>)}</ol> : <p className="sequence-empty">Belum ada urutan khusus. Render akan menggunakan rentang aktif.</p>}
                      {clipSequence.length>0 && <button className="sequence-clear" onClick={()=>{setClipSequence([]);setSelectedSequenceIndex(null);}}>Clear sequence</button>}
                      {clipSequence.length>0 && <div className="sequence-visual" aria-label="Visual clip sequence">{clipSequence.map((clip,index)=><button type="button" key={String(clip.start)+"-"+String(clip.end)+"-"+index} title={"Pilih klip "+(index+1)+": "+formatTimestamp(clip.start)+" – "+formatTimestamp(clip.end)} aria-label={"Pilih klip "+(index+1)} onClick={()=>selectSequenceClip(clip,index)} className={selectedSequenceIndex===index?"selected":""}><b>{String(index+1).padStart(2,"0")}</b><span>{formatDuration(clip.end-clip.start)}</span><small>{(clip.crop_zoom ?? 1).toFixed(1)}× crop</small></button>)}</div>}
                    </div>
                    <div className="trim-grid">
                      <label>In point (seconds)<input type="number" min="0" step="0.1" value={editStart} onChange={e=>updateEditorTrim("start",Number(e.target.value))}/></label>
                      <label>Out point (seconds)<input type="number" min={editStart+0.1} step="0.1" value={editEnd} onChange={e=>updateEditorTrim("end",Number(e.target.value))}/></label>
                    </div>
                  </div>
                </div>
                <aside className="editor-settings">
                  <div className="editor-panel-head"><div><strong>Clip settings</strong><small>Basic edit controls</small></div></div>
                  <label>AI candidate<select value={editorCandidateId} onChange={e=>{setEditorCandidateId(e.target.value);if(!e.target.value){setEditStart(0);setEditEnd(media.find(item=>item.id===aiMediaId)?.duration ?? transcription?.duration ?? 0);}}}><option value="">Manual / full source</option>{(discovery?.candidates ?? []).map(c=><option key={c.id} value={c.id}>{c.title} · {c.score}/100</option>)}</select></label>
                  <div className="export-profile-controls">
                    <div className="export-profile-heading"><strong>Export profile</strong><small>Platform framing and render quality</small></div>
                    <label>Platform preset<select value={outputPreset} onChange={event=>{
                      const preset=event.target.value;
                      setOutputPreset(preset);
                      if(preset==="youtube_shorts"||preset==="instagram_reels"||preset==="facebook_reels")setAspectRatio("9:16");
                      else if(preset==="square_feed")setAspectRatio("1:1");
                      else if(preset==="landscape")setAspectRatio("16:9");
                    }}>
                      <option value="youtube_shorts">YouTube Shorts · 9:16</option>
                      <option value="instagram_reels">Instagram Reels · 9:16</option>
                      <option value="facebook_reels">Facebook Reels · 9:16</option>
                      <option value="square_feed">Square feed · 1:1</option>
                      <option value="landscape">Landscape · 16:9</option>
                      <option value="custom">Custom</option>
                    </select></label>
                    {outputPreset==="custom" && <label>Aspect ratio<select value={aspectRatio} onChange={event=>setAspectRatio(event.target.value)}><option value="9:16">9:16 · Vertical</option><option value="16:9">16:9 · Landscape</option><option value="1:1">1:1 · Square</option></select></label>}
                    <label>Encoding quality<select value={qualityProfile} onChange={event=>setQualityProfile(event.target.value)}><option value="balanced">Balanced · recommended</option><option value="high">High quality</option><option value="maximum">Maximum quality · slower</option></select></label>
                    <label>Frame rate<select value={frameRate===null?"source":String(frameRate)} onChange={event=>setFrameRate(event.target.value==="source"?null:Number(event.target.value))}><option value="source">Follow source</option><option value="24">24 FPS</option><option value="30">30 FPS</option><option value="60">60 FPS</option></select></label>
                    <label className="normalize-audio-toggle"><input type="checkbox" checked={normalizeAudio} onChange={event=>setNormalizeAudio(event.target.checked)}/><span><b>Normalize audio</b><small>EBU loudness target: −16 LUFS / −1.5 dBTP</small></span></label>
                  </div>
                  <div className="crop-controls">
                    <div className="crop-title"><strong>Crop &amp; reposition</strong><button type="button" onClick={()=>updateCropFraming({crop_zoom:1,crop_x:0.5,crop_y:0.5})}>Reset</button></div>
                    <label>Zoom <output>{cropZoom.toFixed(2)}×</output><input type="range" min="1" max="3" step="0.05" value={cropZoom} onChange={e=>updateCropFraming({crop_zoom:Number(e.target.value)})}/></label>
                    <label>Horizontal position <output>{Math.round(cropX*100)}%</output><input type="range" min="0" max="1" step="0.01" value={cropX} onChange={e=>updateCropFraming({crop_x:Number(e.target.value)})}/></label>
                    <label>Vertical position <output>{Math.round(cropY*100)}%</output><input type="range" min="0" max="1" step="0.01" value={cropY} onChange={e=>updateCropFraming({crop_y:Number(e.target.value)})}/></label>
                    <small>{selectedSequenceIndex !== null ? `Framing khusus untuk klip sequence ${selectedSequenceIndex+1}.` : "Framing untuk rentang aktif dan klip baru."} Preview dan hasil render mengikuti pengaturan ini.</small>
                  </div>
                  <div className="editor-info"><span>Selected duration</span><strong>{formatDuration(editorDuration)}</strong></div>
                  <div className="editor-info"><span>Subtitle segments</span><strong>{visibleSubtitleSegments.length}</strong></div>
                  <button className="primary-button editor-save" onClick={saveEditorDraft} disabled={editEnd<=editStart}>Save editing draft</button>
                  <button className="secondary-button editor-save" onClick={()=>void startExport()} disabled={exportBusy || editEnd<=editStart}>{exportBusy ? "Starting export..." : clipSequence.length ? `Render ${clipSequence.length} clips` : "Render this clip"} <ArrowDownToLine size={14}/></button>
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
                <div className="subtitle-toolbar"><label>Subtitle preset<select value={subtitlePreset} onChange={e=>setSubtitlePreset(e.target.value)}><option value="clean">Clean · minimal</option><option value="bold">Bold · high contrast</option><option value="highlight">Highlight · creator style</option></select></label><label className="subtitle-burn-toggle"><input type="checkbox" checked={burnSubtitles} onChange={e=>setBurnSubtitles(e.target.checked)}/> Burn subtitles into exported video</label><span>{visibleSubtitleSegments.length} baris dalam rentang klip</span><button className="primary-button" onClick={saveEditorDraft}>Save subtitle edits</button></div>
                <div className="subtitle-style-controls">
                  <label><span>Ukuran font <output>{subtitleFontSize}px</output></span><input type="range" min="24" max="120" step="1" value={subtitleFontSize} onChange={e=>setSubtitleFontSize(Number(e.target.value))}/></label>
                  <label><span>Posisi dari bawah <output>{subtitlePosition}%</output></span><input type="range" min="5" max="30" step="1" value={subtitlePosition} onChange={e=>setSubtitlePosition(Number(e.target.value))}/></label>
                  <label><span>Ketebalan outline <output>{subtitleOutline.toFixed(1)}</output></span><input type="range" min="0" max="8" step="0.5" value={subtitleOutline} onChange={e=>setSubtitleOutline(Number(e.target.value))}/></label>
                  <label><span>Opasitas background <output>{subtitleBackground}%</output></span><input type="range" min="0" max="90" step="5" value={subtitleBackground} onChange={e=>setSubtitleBackground(Number(e.target.value))}/></label>
                </div>
                <div className={"subtitle-sample " + subtitlePreset} style={{alignItems:"end",paddingBottom:String(subtitlePosition)+"%"}}><span style={{fontSize:String(Math.max(12,Math.round(subtitleFontSize*0.36)))+"px",WebkitTextStroke:String(Math.min(2,subtitleOutline*0.25))+"px #000",backgroundColor:subtitleBackground?"rgba(0,0,0,"+(subtitleBackground/100)+")":(subtitlePreset==="highlight"?undefined:"transparent"),padding:subtitleBackground?"5px 9px":undefined}}>{visibleSubtitleSegments.length ? (subtitleText[transcription.segments.indexOf(visibleSubtitleSegments[0])] ?? visibleSubtitleSegments[0].text) : "Preview subtitle"}</span></div>
                <div className="subtitle-list">{visibleSubtitleSegments.map(segment=>{const index=timelineTranscriptSegments.indexOf(segment);const timing=getSubtitleRange(index,segment);return <div className="subtitle-row" key={index}><div className="subtitle-timing-fields"><button className="subtitle-time" onClick={()=>seekPreview(timing.start)}>{formatTimestamp(timing.start)}</button><label><span>IN</span><input type="number" min="0" max={timing.end-0.05} step="0.05" value={Number(timing.start.toFixed(2))} onChange={event=>updateSubtitleRange(index,"start",Number(event.target.value))}/></label><label><span>OUT</span><input type="number" min={timing.start+0.05} max={editorSourceDuration || undefined} step="0.05" value={Number(timing.end.toFixed(2))} onChange={event=>updateSubtitleRange(index,"end",Number(event.target.value))}/></label></div><textarea rows={2} value={subtitleText[index] ?? segment.text} onChange={e=>setSubtitleText(previous=>({...previous,[index]:e.target.value}))} aria-label={"Subtitle at " + formatTimestamp(timing.start)}/>{segment.words?.length ? <div className="word-edit-list"><strong>{hasCompleteWordTiming(segment) ? "Word timing" : "Word timing · partial (export falls back to segment)"}</strong>{segment.words.map((word,wordIndex)=>{const edited=currentWordTiming(segment.id,wordIndex,word);const key=wordKey(segment.id,wordIndex);return <div className="word-edit-row" key={wordIndex}><span>{wordIndex+1}. {edited.text}</span><label>IN<input type="number" step="0.01" min={segment.start} max={edited.end-0.01} value={Number(edited.start.toFixed(2))} disabled={Boolean(wordBusy)} onChange={event=>setWordTiming(previous=>({...previous,[key]:{...edited,start:Number(event.target.value)}}))}/></label><label>OUT<input type="number" step="0.01" min={edited.start+0.01} max={segment.end} value={Number(edited.end.toFixed(2))} disabled={Boolean(wordBusy)} onChange={event=>setWordTiming(previous=>({...previous,[key]:{...edited,end:Number(event.target.value)}}))}/></label><button className="secondary-button" disabled={!segment.id || Boolean(wordBusy)} onClick={()=>void saveWordTiming(segment,wordIndex,edited)}>{wordBusy===key?"Saving…":"Save"}</button></div>})}</div> : <small className="muted-label">Word timestamps unavailable · segment timing fallback</small>}</div>})}</div>
                <p className="editor-disclaimer">Subtitle yang dikoreksi dan preset akan ikut dirender saat opsi burn-in diaktifkan. Tanpa opsi tersebut, video diekspor tanpa subtitle tertanam.</p>
              </div>}
            </section>
          ) : active === "Export Center" ? (
            <section className="export-view">
              <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> RENDER & DELIVERY</div><h1>Export Center.</h1><p className="welcome-copy">Render klip ke MP4 H.264 untuk Shorts, Reels, dan format lainnya.</p></div><button className="secondary-button" onClick={()=>void loadExports()}><RefreshCw size={14}/> Refresh</button></div>
              {error && <div className="error-banner">{error}</div>}
              <div className="export-create-panel">
                <div><strong>Export current edit</strong><p>Gunakan source video dan rentang yang sedang dipilih di Clip Editor.</p></div>
                {aiMediaId && media.some(item=>item.id===aiMediaId) ? <div className="export-config"><span>{media.find(item=>item.id===aiMediaId)?.original_name}</span><span>{clipSequence.length ? clipSequence.length + " clips in sequence" : formatTimestamp(editStart) + " – " + formatTimestamp(editEnd)} · {aspectRatio}</span><span>{burnSubtitles ? "Burn-in " + visibleSubtitleSegments.length + " subtitle · " + subtitlePreset : "Subtitle burn-in off"}</span><button className="primary-button" onClick={()=>void startExport()} disabled={exportBusy || editEnd<=editStart}>{exportBusy ? "Starting..." : "Render MP4"} <ArrowDownToLine size={14}/></button></div> : <button className="subtle-button" onClick={()=>setActive("AI Workspace")}>Pilih source di AI Workspace <ArrowRight size={14}/></button>}
              </div>
              <div className="export-list-heading"><div><h3>Export history</h3><p>100 pekerjaan terbaru dari perangkat ini</p></div><span>{exportJobs.length} jobs</span></div>
              {exportJobs.length ? <div className="export-job-list">{exportJobs.map(job=><article className="export-job-card" key={job.id}><div className="export-job-main"><div><strong>{job.output_name}</strong><small>{job.clip_count > 1 ? job.clip_count + " clips in sequence" : formatTimestamp(job.start_seconds) + " – " + formatTimestamp(job.end_seconds)} · {job.output_preset ?? job.aspect_ratio} · {job.quality_profile ?? "balanced"} · {job.frame_rate ? job.frame_rate+" FPS" : "Source FPS"} · {job.normalize_audio ? "Normalized audio" : "Original loudness"} · {job.encoder ?? "Encoder pending"}</small></div><span className={"export-status "+job.status}>{job.status}</span></div><div className="progress-track"><i style={{width:Math.max(0,Math.min(100,job.progress*100))+"%"}}/></div>{job.error && <small className="job-error">{job.error}</small>}{job.status==="completed" && job.download_url && <a className="export-download" href={API_BASE+job.download_url}>Download MP4 <ArrowDownToLine size={13}/></a>}{["queued","processing"].includes(job.status) && <button className="secondary-button export-cancel" onClick={async()=>{try{const response=await fetch(`${API_BASE}/api/exports/${job.id}/cancel`,{method:"POST"});const body=await response.json();if(!response.ok)throw new Error(body.detail??"Gagal membatalkan export.");setExportJobs(previous=>previous.map(item=>item.id===job.id?body as ExportJob:item));}catch(e){setError(e instanceof Error?e.message:"Gagal membatalkan export.");}}}>Cancel export</button>}</article>)}</div> : <div className="empty-panel ai-empty"><strong>Belum ada hasil export</strong><p>Atur rentang klip di Clip Editor lalu mulai rendering.</p><button className="subtle-button" onClick={()=>setActive("Clip Editor")}>Buka Clip Editor <ArrowRight size={14}/></button></div>}
            </section>
          ) : active === "Settings" ? (
            <section className="runtime-settings">
              <div className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> SYSTEM READINESS</div><h1>System check.</h1><p className="welcome-copy">Periksa komponen yang dibutuhkan sebelum mulai clipping. Software yang belum tersedia akan disertai tautan unduhan resmi.</p></div><button className="secondary-button" onClick={()=>void loadRuntimeDiagnostics()} disabled={diagnosticsLoading}><RefreshCw size={14}/>{diagnosticsLoading ? "Memeriksa..." : "Check again"}</button></div>
              {diagnosticsError && <div className="error-banner">{diagnosticsError}<button className="text-button" onClick={()=>void loadRuntimeDiagnostics()}>Retry</button></div>}
              {!runtimeDiagnostics && diagnosticsLoading ? <div className="loading-state">Memeriksa software dan runtime lokal...</div> : null}
              {runtimeDiagnostics && <>
                <div className="runtime-summary"><div><span className="muted-label">PLATFORM</span><strong>{runtimeDiagnostics.platform === "nt" ? "Windows" : runtimeDiagnostics.platform}</strong></div><div><span className="muted-label">REQUIRED COMPONENTS</span><strong>{runtimeDiagnostics.dependencies.filter(item=>item.required && item.available).length}/{runtimeDiagnostics.dependencies.filter(item=>item.required).length} ready</strong></div><div><span className="muted-label">AI PROVIDER</span><strong>{runtimeDiagnostics.ai.provider} · {runtimeDiagnostics.ai.configured ? "configured" : "needs setup"}</strong></div></div>
                <div className="runtime-section-heading"><div><h3>Software & runtime</h3><p>Komponen wajib dan opsional yang terdeteksi oleh aplikasi.</p></div><span>{runtimeDiagnostics.dependencies.filter(item=>item.required && !item.available).length ? "Action required" : "Required checks passed"}</span></div>
                <div className="runtime-dependency-list">{runtimeDiagnostics.dependencies.map(item=><article className="runtime-dependency-card" key={item.id}><div className="runtime-dependency-top"><div><span className="runtime-dependency-kind">{item.required ? "REQUIRED" : "OPTIONAL"}</span><h4>{item.name}</h4></div><span className={"runtime-status "+(item.available ? "ready" : item.required ? "missing" : "optional")}>{item.available ? "Available" : item.required ? "Not detected" : "Optional · not installed"}</span></div><p>{item.description}</p>{item.id === "ffmpeg" && <small className="runtime-version-info">FFmpeg: {runtimeDiagnostics.ffmpeg.available ? runtimeDiagnostics.ffmpeg.version ?? "Detected" : "Missing"} · FFprobe: {runtimeDiagnostics.ffprobe.available ? runtimeDiagnostics.ffprobe.version ?? "Detected" : "Missing"}</small>}{!item.available && <div className="runtime-install-note">{item.install_note}</div>}{!item.available && item.download_url && <a className="runtime-download-link" href={item.download_url} target="_blank" rel="noreferrer">{item.download_label ?? "Open download page"} <ArrowRight size={14}/></a>}</article>)}</div>
                <div className="runtime-section-heading"><div><h3>AI & transcription</h3><p>Konfigurasi provider dan mesin transkripsi.</p></div></div>
                <div className="runtime-detail-grid"><article className="runtime-detail-card"><span className="muted-label">WHISPER ENGINE</span><strong>{runtimeDiagnostics.transcription.installed ? "Installed" : "Unavailable"}</strong><p>Device: {runtimeDiagnostics.transcription.device} · Compute: {runtimeDiagnostics.transcription.compute_type}</p><small>Model Whisper diunduh otomatis saat pertama kali digunakan; ukuran unduhan bergantung model yang dipilih.</small></article><article className="runtime-detail-card"><span className="muted-label">AI PROVIDER</span><strong>{runtimeDiagnostics.ai.provider} · {runtimeDiagnostics.ai.configured ? "Configured" : "Needs configuration"}</strong><p>Model: {runtimeDiagnostics.ai.model ?? "Default / not set"}</p><small>{runtimeDiagnostics.ai.provider === "ollama" ? "Pemeriksaan ini belum menguji koneksi Ollama atau memastikan model tersedia." : "Gemini memerlukan API key. Pemeriksaan ini tidak menampilkan atau mengirim API key."}</small></article></div>
                <p className="runtime-footnote">Catatan: pemeriksaan ini membaca kondisi lokal, bukan menginstal software secara otomatis. Setelah memasang FFmpeg, pastikan folder bin masuk ke PATH lalu restart aplikasi dan tekan Check again. Python runtime sudah dibundel pada installer Windows.</p>
              </>}
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
          <footer><span>PERSONAL CLIPPER STUDIO <b>•</b> PHASE 5 BUILD</span><span>LOCAL-FIRST BY DESIGN</span></footer>
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
