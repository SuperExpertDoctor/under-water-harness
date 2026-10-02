import { useCallback, useEffect, useRef, useState } from "react";
import { missionApi } from "../api/missionApi";
import { connectStateStream } from "../api/websocketApi";
import { mergeMissionState, mergeTelemetryFrame, shouldApplySnapshot } from "../../state/missionState";

export default function useMissionControl(enabled) {
  const [state, setState] = useState({});
  const [frame, setFrame] = useState(null);
  const [ready, setReady] = useState(false);
  const [connection, setConnection] = useState("connecting");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [tasks, setTasks] = useState([]);
  const [skills, setSkills] = useState([]);
  const [candidate, setCandidate] = useState(null);
  const [assessment, setAssessment] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const episodeRef = useRef(null);
  const refreshRef = useRef(null);
  const generation = useRef(0);

  useEffect(() => {
    if (!enabled) { setReady(false); return undefined; }
    let disposed = false;
    let stop;
    let timer;
    let inFlight = false;
    let authenticated = false;
    const accept = (next) => {
      if (disposed || !next.episode_id) return;
      if (episodeRef.current !== next.episode_id) {
        episodeRef.current = next.episode_id;
        generation.current += 1;
        setCandidate(null);
        setAssessment(null);
        setReceipt(null);
        setTasks([]);
        setError("");
      }
      setState((current) => mergeMissionState(current, next));
    };
    const refresh = async () => {
      if (inFlight || disposed) return;
      inFlight = true;
      try {
        await missionApi.health();
        if (disposed) return;
        if (!authenticated) {
          authenticated = true;
          setReady(true);
          stop = connectStateStream({ onState: accept, onStatus: setConnection });
        }
        const requestedEpisode = episodeRef.current;
        const snapshot = await missionApi.get("/api/state");
        if (disposed || !shouldApplySnapshot(requestedEpisode, episodeRef.current, snapshot.episode_id)) return;
        accept({ episode_id: snapshot.episode_id, cursor: snapshot.event_cursor, plans: snapshot.plans,
          autonomy_mode: snapshot.autonomy_mode, agent: snapshot.agent_status, events: snapshot.events });
        setFrame((current) => mergeTelemetryFrame(current, snapshot));
        const currentEpisode = snapshot.episode_id;
        const [messages, jobs, taskList, skillList] = await Promise.all([
          missionApi.get("/api/pi-agent/messages"), missionApi.get("/api/pi-agent/task-assignment"),
          missionApi.get("/api/task-assembly/tasks"), missionApi.get("/api/skills"),
        ]);
        if (disposed || episodeRef.current !== currentEpisode) return;
        setState((current) => mergeMissionState(current, { episode_id: currentEpisode, messages: messages.messages, jobs: jobs.assignments }));
        setTasks(taskList.tasks || []);
        setSkills(skillList.skills || []);
      } catch (failure) {
        if (!disposed) { setError(failure.message); setConnection("reconnecting"); }
      } finally { inFlight = false; }
    };
    refreshRef.current = refresh;
    refresh();
    timer = window.setInterval(refresh, 2500);
    return () => {
      disposed = true;
      generation.current += 1;
      stop?.();
      window.clearInterval(timer);
      refreshRef.current = null;
    };
  }, [enabled]);

  const act = useCallback(async (label, path, data = {}, method = "post") => {
    if (!ready || (busy && path !== "/api/simulation/stop")) return null;
    const currentGeneration = generation.current;
    setBusy(label);
    setError("");
    try {
      const result = await missionApi.mutate(path, episodeRef.current, !enabled, data, method);
      if (generation.current !== currentGeneration) return null;
      setReceipt(result);
      await refreshRef.current?.();
      return result;
    } catch (failure) {
      if (generation.current === currentGeneration) setError(failure.message);
      return null;
    } finally { setBusy(""); }
  }, [busy, enabled, ready]);

  const preview = useCallback(async (id) => {
    const currentGeneration = generation.current;
    try {
      const result = await missionApi.get(`/api/plans/${encodeURIComponent(id)}`);
      if (generation.current === currentGeneration && result.episode_id === episodeRef.current) {
        setCandidate(result);
        setAssessment(null);
      }
    } catch (failure) { setError(failure.message); }
  }, []);

  return { state, frame, ready, connection, error, busy, tasks, skills, candidate, assessment, receipt,
    act, preview, setCandidate, setAssessment, clearError: () => setError("") };
}
