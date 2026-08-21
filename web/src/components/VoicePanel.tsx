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
  /** Spoken questions answered so far, and how many there are in total. The
   *  seller needs to see the end coming; an open-ended conversation with no
   *  visible finish line is the thing people bail out of. */
  covered?: number;
  total?: number;
  /** "voice" asks only the routed questions. "all" asks everything still
   *  unanswered, for a seller who chose to do the whole form by talking. */
  scope?: "voice" | "all";
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
export function VoicePanel({
  token, onAnswerRecorded, onFinished, disabled, covered = 0, total = 0, scope = "voice",
}: Props) {
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
  const audioCtx = useRef<AudioContext | null>(null);

  // Held in a ref so `stop` has a stable identity. It used to close over
  // `onFinished`; the parent passes an inline arrow, so `stop` changed on every
  // render, the cleanup effect re-ran, and the live WebRTC call was torn down
  // after the seller's very first answer.
  const onFinishedRef = useRef(onFinished);
  // The realtime API allows one response at a time. A grouped answer produces
  // several record_answer calls in a single turn, so asking for a response
  // after each one asks six times too often.
  const wholeForm = scope === "all";
  const responseActive = useRef(false);
  const wantTurn = useRef(false);
  // Has the seller said anything since the assistant's last turn? Given a tool
  // that takes a list, the model will happily read out a group and record it in
  // the same breath, then run on through the rest of the form. Answers nobody
  // gave do not belong on a disclosure, so a turn in which the seller has not
  // spoken cannot write.
  const sellerSpoke = useRef(false);
  // What the assistant said this turn, and how many times we have had to prod
  // it to actually ask the question it just announced.
  const turnText = useRef("");
  const nudges = useRef(0);
  const calledTool = useRef(false);
  onFinishedRef.current = onFinished;

  const stop = useCallback(
    (finished = false) => {
      window.clearInterval(countdown.current);
      if (raf.current) cancelAnimationFrame(raf.current);
      // Chrome caps a page at six AudioContexts; without this, six start/stops
      // and the mic meter stops working for the rest of the session.
      audioCtx.current?.close().catch(() => undefined);
      audioCtx.current = null;
      dc.current?.close();
      pc.current?.close();
      stream.current?.getTracks().forEach((t) => t.stop());
      pc.current = null;
      dc.current = null;
      stream.current = null;
      responseActive.current = false;
      wantTurn.current = false;
      sellerSpoke.current = false;
      setLevel(0);
      setSecondsLeft(null);
      setPhase("idle");
      if (finished) onFinishedRef.current();
    },
    [],
  );

  useEffect(() => () => stop(), [stop]);

  /** Ask for the next turn, but only ever one at a time.
   *
   *  The realtime API does not generate a response off the back of a
   *  function_call_output. Without a follow-up `response.create`, the assistant
   *  records the seller's first answer and then never speaks again - it looks
   *  like a hang, and it takes the entire voice lane down with it.
   *
   *  But it also refuses a second `response.create` while one is running. Now
   *  that a single "range, oven and dishwasher, none of the rest" produces
   *  seven tool calls, asking after each one meant six rejections and an error
   *  banner in the seller's face. So: ask now if nothing is running, otherwise
   *  remember that we owe a turn and ask when the current one finishes. */
  const requestTurn = useCallback(() => {
    const channel = dc.current;
    if (!channel || channel.readyState !== "open") return;
    if (responseActive.current) {
      wantTurn.current = true;
      return;
    }
    responseActive.current = true;
    channel.send(JSON.stringify({ type: "response.create" }));
  }, []);

  const sendToolResult = useCallback((callId: string, output: unknown) => {
    const channel = dc.current;
    if (!channel || channel.readyState !== "open") return;
    channel.send(
      JSON.stringify({
        type: "conversation.item.create",
        item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) },
      }),
    );
    requestTurn();
  }, [requestTurn]);

  const handleToolCall = useCallback(
    async (name: string, args: any, callId: string) => {
      if (name === "finish_section") {
        stop(true);
        return;
      }
      if (!sellerSpoke.current) {
        sendToolResult(callId, {
          ok: false,
          error:
            "The seller has not said anything since your last turn. Ask, then " +
            "wait for their answer. Do not record anything they have not said.",
        });
        return;
      }

      // A whole run-through group in one request. Seven separate calls meant
      // seven round-trips before the assistant could speak again, which the
      // seller experiences as several seconds of silence after answering.
      if (name === "record_group") {
        try {
          const res = await api.post<{
            recorded: { questionId: string }[];
            refused: { questionId: string | null; error: string }[];
            next: unknown;
          }>(`/api/voice/${token}/answers`, args);
          const ids = res.recorded.map((r) => r.questionId);
          if (ids.length) {
            setSaved((s) => [...s, ...ids].slice(-4));
            ids.forEach(onAnswerRecorded);
          }
          // `next` is the whole point: the assistant kept ending its turn on
          // "ready to move on to safety and security" and leaving the seller
          // waiting. The next question rides back with the last one.
          sendToolResult(callId, {
            ok: true,
            recorded: ids.length,
            ...(res.refused.length ? { refused: res.refused } : {}),
            next: res.next,
          });
        } catch (e: any) {
          sendToolResult(callId, { ok: false, error: e?.message ?? "rejected" });
        }
        return;
      }

      if (name !== "record_answer") return;
      try {
        const res = await api.post<{ questionId: string; next: unknown }>(
          `/api/voice/${token}/answer`, args,
        );
        setSaved((s) => [...s.slice(-4), res.questionId]);
        onAnswerRecorded(res.questionId);
        sendToolResult(callId, { ok: true, next: res.next });
      } catch (e: any) {
        // Tell the model it failed so it re-asks, rather than believing it saved.
        sendToolResult(callId, { ok: false, error: e?.message ?? "rejected" });
      }
    },
    [token, onAnswerRecorded, stop, sendToolResult],
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
          turnText.current += ev.delta ?? "";
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
            sellerSpoke.current = true;
            setTurns((t) => [...t, { who: "seller", text: ev.transcript.trim() }]);
          }
          break;
        case "response.function_call_arguments.done":
          calledTool.current = true;
          try {
            handleToolCall(ev.name, JSON.parse(ev.arguments || "{}"), ev.call_id);
          } catch {
            /* malformed arguments: the model will be told and can retry */
          }
          break;
        case "response.created":
          responseActive.current = true;
          break;
        case "response.done": {
          responseActive.current = false;
          // One utterance may legitimately answer several things, so the gate
          // clears per turn rather than per recorded answer.
          sellerSpoke.current = false;

          const spoken = turnText.current;
          const usedTool = calledTool.current;
          turnText.current = "";
          calledTool.current = false;

          if (wantTurn.current) {
            wantTurn.current = false;
            requestTurn();
            break;
          }

          // "Got it - let me ask the next group of safety and security questions
          // in one shot." ...and then it stops, and the seller sits there
          // waiting for a question that never comes until they prod it. Telling
          // it not to do this in the brief and again in every tool result both
          // help and neither is reliable, so if a turn ends on an announcement
          // rather than a question, take another turn immediately.
          const dangling =
            spoken.trim().length > 0 &&
            !usedTool &&
            !spoken.includes("?") &&
            /\b(next|move on|moving on|coming up|one shot|go through|run through)\b/i.test(
              spoken.slice(-200),
            );
          if (dangling && nudges.current < 3) {
            nudges.current += 1;
            requestTurn();
          } else if (!dangling) {
            nudges.current = 0;
          }
          break;
        }
        case "error":
          // A turn we asked for while one was already running is our own race,
          // not something the seller did. Recover instead of alarming them.
          if (ev.error?.code === "conversation_already_has_active_response") {
            responseActive.current = true;
            wantTurn.current = true;
            break;
          }
          setError(ev.error?.message ?? "The assistant hit an error.");
          break;
      }
    },
    [handleToolCall, requestTurn],
  );

  const start = useCallback(async () => {
    setError(null);
    setPhase("connecting");
    try {
      const info = await api.post<SessionInfo>(`/api/voice/${token}/session?scope=${scope}`);

      // getUserMedia does not resolve while the browser's permission prompt is
      // open, and that prompt lives in browser chrome the page cannot see. Left
      // alone this card just reads "Connecting..." forever, which looks broken
      // to someone who has not noticed the bubble at the top of their window.
      const hint = window.setTimeout(() => {
        setError(
          "Your browser is asking for permission to use the microphone - look " +
          "for the prompt at the top of the window. You can also just tap the " +
          "answers instead.",
        );
      }, 3500);
      let mic: MediaStream;
      try {
        mic = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
      } finally {
        window.clearTimeout(hint);
      }
      setError(null);
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
      channel.onopen = () => {
        setPhase("live");
        // The assistant opens. With low-eagerness turn detection nobody speaks
        // first unless we ask, so both sides would sit waiting.
        responseActive.current = true;
        channel.send(JSON.stringify({ type: "response.create" }));
      };

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
      audioCtx.current = ctx;
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
            // Not stop(true): advancing would move a seller who is mid-sentence
            // off the question, losing the answer they were describing.
            stop(false);
            setError("The talking session timed out. Start it again, or type your answer below.");
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
  }, [token, onMessage, stop, scope]);

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
            {(phase === "idle" || phase === "error") &&
              (wholeForm ? "Ready when you are" : "Talk it through instead")}
          </div>
          <div className="voice-sub small muted">
            {live
              ? "Say it however it comes out. I'll ask if I need more."
              : wholeForm
                ? "I'll take you through the whole form, a room at a time. Stop or switch to tapping whenever you like."
                : "These are the questions people get stuck on. Speaking is faster than typing, and you can stop any time."}
          </div>
        </div>

        {live && secondsLeft !== null && (
          <span className="chip voice-count" title="Talking time left in this session">
            {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")}
          </span>
        )}
        {total > 1 && (
          <span className="chip voice-count" title="Spoken questions in this section">
            {covered} of {total}
          </span>
        )}
      </div>

      {live && total > 1 && (
        <div className="voice-progress" aria-hidden>
          <span style={{ width: `${Math.min(100, (covered / total) * 100)}%` }} />
        </div>
      )}

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

      {live && (
        <div className="voice-actions">
          <span className="tiny voice-hint">
            {total > 1 && covered >= total
              ? "That is all of them. You can carry on below."
              : total > 1
                ? `${Math.max(total - covered, 0)} left in this section`
                : "Say it however it comes out."}
          </span>
          {/* stop(false), not stop(true). Finishing advances the step; this
              button exists for a seller who wants to type this answer instead,
              so moving them off the question defeats the point. */}
          <button type="button" className="btn voice-done" onClick={() => stop(false)}>
            Stop talking
          </button>
        </div>
      )}

      {!live && saved.length > 0 && (
        <div className="voice-saved tiny muted">
          Saved as you go: {saved.length} answer{saved.length === 1 ? "" : "s"} recorded.
        </div>
      )}
    </div>
  );
}
