import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import "./voice.css";

type Phase = "idle" | "connecting" | "live" | "ending" | "error";

interface Turn { who: "seller" | "assistant"; text: string }

interface Props {
  token: string;
  /** Called after the agent records an answer, so the form can refresh. */
  onAnswerRecorded: (questionId: string) => void;
  onFinished: () => void;
  disabled?: boolean;
}

interface SessionInfo { clientSecret: string; model: string; maxSeconds: number }

/**
 * Voice runs peer-to-peer with OpenAI over WebRTC. Audio never touches our
 * server; the only thing the page is given is a short-lived client secret minted
 * per session, so the standing API key stays server-side.
 *
 * Tool calls come back over the data channel and are POSTed to our API rather
 * than applied locally, because the browser is not a trusted writer just because
 * a model is driving it. The server re-checks the question exists, is currently
 * visible, and coerces the value into the shape the form can hold.
 */
export function VoicePanel({ token, onAnswerRecorded, onFinished, disabled }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [level, setLevel] = useState(0);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const [saved, setSaved] = useState<string[]>([]);

  const pc = useRef<RTCPeerConnection | null>(null);
  const dc = useRef<RTCDataChannel | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const audioEl = useRef<HTMLAudioElement | null>(null);
  const raf = useRef<number>();
  const countdown = useRef<number>();
  const partial = useRef<string>("");

  const stop = useCallback(
    (finished = false) => {
      window.clearInterval(countdown.current);
      if (raf.current) cancelAnimationFrame(raf.current);
      dc.current?.close();
      pc.current?.close();
      stream.current?.getTracks().forEach((t) => t.stop());
      pc.current = null;
      dc.current = null;
      stream.current = null;
      setLevel(0);
      setSecondsLeft(null);
      setPhase("idle");
      if (finished) onFinished();
    },
    [onFinished],
  );

  useEffect(() => () => stop(), [stop]);

  const handleToolCall = useCallback(
    async (name: string, args: any, callId: string) => {
      if (name === "finish_section") {
        stop(true);
        return;
      }
      if (name !== "record_answer") return;
      try {
        const res = await api.post<{ questionId: string }>(`/api/voice/${token}/answer`, args);
        setSaved((s) => [...s.slice(-4), res.questionId]);
        onAnswerRecorded(res.questionId);
        dc.current?.send(
          JSON.stringify({
            type: "conversation.item.create",
            item: {
              type: "function_call_output",
              call_id: callId,
              output: JSON.stringify({ ok: true }),
            },
          }),
        );
      } catch (e: any) {
        // Tell the model it failed so it can re-ask, rather than believing it saved.
        dc.current?.send(
          JSON.stringify({
            type: "conversation.item.create",
            item: {
              type: "function_call_output",
              call_id: callId,
              output: JSON.stringify({ ok: false, error: e?.message ?? "rejected" }),
            },
          }),
        );
      }
    },
    [token, onAnswerRecorded, stop],
  );

  const onMessage = useCallback(
    (raw: MessageEvent) => {
      let ev: any;
      try {
        ev = JSON.parse(raw.data);
      } catch {
        return;
      }
      switch (ev.type) {
        case "response.output_audio_transcript.delta":
        case "response.audio_transcript.delta":
          partial.current += ev.delta ?? "";
          setTurns((t) => {
            const next = [...t];
            const last = next[next.length - 1];
            if (last?.who === "assistant") last.text = partial.current;
            else next.push({ who: "assistant", text: partial.current });
            return next;
          });
          break;
        case "response.output_audio_transcript.done":
        case "response.audio_transcript.done":
          partial.current = "";
          break;
        case "conversation.item.input_audio_transcription.completed":
          if (ev.transcript?.trim()) {
            setTurns((t) => [...t, { who: "seller", text: ev.transcript.trim() }]);
          }
          break;
        case "response.function_call_arguments.done":
          try {
            handleToolCall(ev.name, JSON.parse(ev.arguments || "{}"), ev.call_id);
          } catch {
            /* malformed arguments: the model will be told and can retry */
          }
          break;
        case "error":
          setError(ev.error?.message ?? "The assistant hit an error.");
          break;
      }
    },
    [handleToolCall],
  );

  const start = useCallback(async () => {
    setError(null);
    setPhase("connecting");
    try {
      const info = await api.post<SessionInfo>(`/api/voice/${token}/session`);

      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      stream.current = mic;

      const conn = new RTCPeerConnection();
      pc.current = conn;

      conn.ontrack = (e) => {
        if (audioEl.current) audioEl.current.srcObject = e.streams[0];
      };
      mic.getTracks().forEach((t) => conn.addTrack(t, mic));

      const channel = conn.createDataChannel("oai-events");
      dc.current = channel;
      channel.onmessage = onMessage;
      channel.onopen = () => setPhase("live");

      const offer = await conn.createOffer();
      await conn.setLocalDescription(offer);

      const res = await fetch(`https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(info.model)}`, {
        method: "POST",
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${info.clientSecret}`,
          "Content-Type": "application/sdp",
        },
      });
      if (!res.ok) throw new Error(`Could not connect (${res.status})`);
      await conn.setRemoteDescription({ type: "answer", sdp: await res.text() });

      // Mic level, purely so the seller can see they are being heard.
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(mic).connect(analyser);
      const buf = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128) / 128);
        setLevel((l) => l * 0.7 + peak * 0.3);
        raf.current = requestAnimationFrame(tick);
      };
      tick();

      setSecondsLeft(info.maxSeconds);
      countdown.current = window.setInterval(() => {
        setSecondsLeft((s) => {
          if (s === null) return null;
          if (s <= 1) {
            stop(true);
            return null;
          }
          return s - 1;
        });
      }, 1000);
    } catch (e: any) {
      setError(
        e?.name === "NotAllowedError"
          ? "Your browser blocked the microphone. You can answer these by tapping instead."
          : e?.message ?? "Could not start the assistant.",
      );
      setPhase("error");
      stream.current?.getTracks().forEach((t) => t.stop());
    }
  }, [token, onMessage, stop]);

  const live = phase === "live";

  return (
    <div className={`voice ${live ? "is-live" : ""}`}>
      <audio ref={audioEl} autoPlay />

      <div className="voice-head">
        <button
          type="button"
          className={`orb ${live ? "is-live" : ""} ${phase === "connecting" ? "is-connecting" : ""}`}
          onClick={() => (live ? stop(false) : start())}
          disabled={disabled || phase === "connecting"}
          aria-label={live ? "End the conversation" : "Start talking"}
        >
          <span className="orb-ring" style={{ transform: `scale(${1 + Math.min(level, 0.6) * 0.7})` }} />
          {live ? (
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
              <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="21" height="21" aria-hidden>
              <path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z" fill="currentColor" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" fill="none" stroke="currentColor"
                    strokeWidth="1.9" strokeLinecap="round" />
            </svg>
          )}
        </button>

        <div className="voice-copy">
          <div className="voice-title">
            {phase === "connecting" && "Connecting…"}
            {live && "Listening"}
            {(phase === "idle" || phase === "error") && "Talk it through instead"}
          </div>
          <div className="voice-sub small muted">
            {live
              ? "Say it however it comes out. I'll ask if I need more."
              : "These are the questions people get stuck on. Speaking is faster than typing, and you can stop any time."}
          </div>
        </div>

        {live && secondsLeft !== null && (
          <span className="chip chip-sage voice-timer">
            {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")}
          </span>
        )}
      </div>

      {error && <div className="voice-error small">{error}</div>}

      {turns.length > 0 && (
        <div className="voice-transcript" aria-live="polite">
          {turns.slice(-6).map((t, i) => (
            <div key={i} className={`turn turn-${t.who}`}>
              <span className="turn-who tiny">{t.who === "seller" ? "You" : "Assistant"}</span>
              <p>{t.text}</p>
            </div>
          ))}
        </div>
      )}

      {saved.length > 0 && (
        <div className="voice-saved tiny muted">
          Saved as you go: {saved.length} answer{saved.length === 1 ? "" : "s"} recorded.
        </div>
      )}
    </div>
  );
}
