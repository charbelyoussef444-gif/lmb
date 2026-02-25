// src/pages/ConversationsPage.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  SendHorizontal,
  Search,
  Loader2,
  Clock,
  MessageSquareHeart,
  ShieldCheck,
  ArrowRight,
  Star,
  Filter,
  Plus,
  MoreVertical,
  Check,
  CheckCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  api,
  type Conversation,
  type Msg,
  type MatchResponse,
  type MatchStatus,
  type ReportReason,
} from "../lib/api";
import { supabase } from "../lib/supabaseClient";

// ✅ use the fixed WS hook
import { useConversationWS, type WsEvent } from "../hooks/useConversationWS";

const MAX_ACTIVE_MATCHES = 3;
const PHOTO_LOCK_HOURS = 24;
const PHOTO_LOCK_MS = PHOTO_LOCK_HOURS * 60 * 60 * 1000;

const PHOTO_UNLOCK_NOTE_BASE =
  "Photos can appear only with mutual consent, and only after 24 hours from the match start.";
const PHOTO_UNLOCK_NOTE_CONSENT =
  "If both users press Yes and are satisfied with the match, photos will appear when the timer ends.";

/** ---------------- Match popup (dopamine animation) ---------------- */
function useElapsedMs(enabled: boolean, startAt: number | null, tick = 80) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!enabled || !startAt) {
      setElapsed(0);
      return;
    }
    let alive = true;
    const t = setInterval(() => {
      if (!alive) return;
      setElapsed(Date.now() - startAt);
    }, tick);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [enabled, startAt, tick]);

  return elapsed;
}

// stable pseudo-random for a given seed (no deps)
function seededRand(seed: number) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}
function clamp(n: number, a: number, b: number) {
  return Math.max(a, Math.min(b, n));
}

function MatchFindingModal({
  open,
  startedAt,
}: {
  open: boolean;
  startedAt: number | null;
}) {
  const elapsed = useElapsedMs(open, startedAt, 90);

  // loop phrases (no “accuracy” stickiness, no “engine”, no “no interruptions”, no “best” copy)
  const stages = useMemo(
    () => [
      { title: "Finding a strong fit…", sub: "Reading your vibe" },
      { title: "Scanning compatibility…", sub: "Personality, interests, intent" },
      { title: "Filtering options…", sub: "Keeping it clean" },
      { title: "Building your thread…", sub: "Preparing the chat" },
      { title: "Locking in…", sub: "Almost ready" },
    ],
    []
  );

  const stageIndex = useMemo(() => {
    const stepMs = 850;
    const i = Math.floor(elapsed / stepMs) % stages.length;
    return i;
  }, [elapsed, stages.length]);

  // Randomize 3 blobs each “turn” smoothly
  const blobTargets = useMemo(() => {
    // stageIndex is the “turn”
    const seedBase = 1000 + stageIndex * 97;
    const make = (k: number) => {
      // position inside a 72x72 “inner box” centered in circle
      const rx = seededRand(seedBase + k * 13);
      const ry = seededRand(seedBase + k * 29);
      const rsz = seededRand(seedBase + k * 41);

      const x = clamp(rx * 64 - 32, -28, 28);
      const y = clamp(ry * 64 - 32, -28, 28);
      const s = 0.75 + rsz * 0.55;

      return { x, y, s };
    };
    return [make(1), make(2), make(3)];
  }, [stageIndex]);

  const stage = stages[stageIndex];

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/65 p-4"
          role="dialog"
          aria-modal="true"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="relative w-full max-w-md overflow-hidden rounded-[28px] border border-white/10 bg-slate-950/70 p-5 shadow-2xl backdrop-blur-xl"
            initial={{ y: 18, opacity: 0, scale: 0.98 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: 14, opacity: 0, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 260, damping: 22 }}
          >
            {/* animated glow */}
            <div className="pointer-events-none absolute inset-0">
              <motion.div
                className="absolute -top-24 left-1/2 h-56 w-56 -translate-x-1/2 rounded-full bg-[radial-gradient(circle_at_center,_rgba(99,102,241,0.35),_transparent_60%)] blur-3xl"
                animate={{ scale: [1, 1.08, 1], opacity: [0.9, 1, 0.9] }}
                transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
              />
              <motion.div
                className="absolute -bottom-28 right-0 h-72 w-72 rounded-full bg-[radial-gradient(circle_at_center,_rgba(244,114,182,0.28),_transparent_60%)] blur-3xl"
                animate={{ scale: [1, 1.06, 1], opacity: [0.85, 1, 0.85] }}
                transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
              />
              <motion.div
                className="absolute -bottom-10 -left-10 h-60 w-60 rounded-full bg-[radial-gradient(circle_at_center,_rgba(139,92,246,0.22),_transparent_60%)] blur-3xl"
                animate={{ scale: [1, 1.1, 1], opacity: [0.8, 1, 0.8] }}
                transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}
              />
            </div>

            {/* top badge (clean) */}
            <div className="relative flex items-center justify-between gap-3">
              <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold text-white/90">
                <motion.span
                  className="grid h-6 w-6 place-items-center rounded-full bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 text-white"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 2.8, repeat: Infinity, ease: "linear" }}
                >
                  <Sparkles className="h-3.5 w-3.5" />
                </motion.span>
                Matching
              </div>

              <div className="text-[11px] font-medium text-white/55"> </div>
            </div>

            {/* centerpiece: big circle with 3 blobs that randomize each turn */}
            <div className="relative mt-6 grid place-items-center">
              <div className="relative">
                {/* rotating halo */}
                <motion.div
                  className="absolute inset-[-12px] rounded-full bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 opacity-70 blur-[12px]"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 3.2, repeat: Infinity, ease: "linear" }}
                />

                <motion.div
                  className="relative grid h-28 w-28 place-items-center rounded-full border border-white/15 bg-white/5 shadow-[0_0_0_1px_rgba(255,255,255,0.06)] overflow-hidden"
                  animate={{ scale: [1, 1.03, 1] }}
                  transition={{ duration: 1.25, repeat: Infinity, ease: "easeInOut" }}
                >
                  {/* orbit dots */}
                  <motion.div
                    className="absolute inset-0"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 2.1, repeat: Infinity, ease: "linear" }}
                  >
                    <div className="absolute left-1/2 top-0 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-white/85 shadow" />
                    <div className="absolute bottom-1 left-3 h-2 w-2 rounded-full bg-white/65 shadow" />
                    <div className="absolute bottom-4 right-2 h-1.5 w-1.5 rounded-full bg-white/55 shadow" />
                  </motion.div>

                  {/* 3 randomized blobs */}
                  <div className="relative h-20 w-20">
                    {blobTargets.map((b, i) => (
                      <motion.div
                        key={`${stageIndex}-${i}`}
                        className={
                          "absolute left-1/2 top-1/2 h-10 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full blur-[1px] " +
                          (i === 0
                            ? "bg-[radial-gradient(circle_at_center,_rgba(99,102,241,0.95),_rgba(99,102,241,0.05))]"
                            : i === 1
                            ? "bg-[radial-gradient(circle_at_center,_rgba(244,114,182,0.9),_rgba(244,114,182,0.05))]"
                            : "bg-[radial-gradient(circle_at_center,_rgba(139,92,246,0.9),_rgba(139,92,246,0.05))]")
                        }
                        initial={{ x: 0, y: 0, scale: 0.9, opacity: 0.85 }}
                        animate={{ x: b.x, y: b.y, scale: b.s, opacity: [0.8, 1, 0.85] }}
                        transition={{
                          x: { type: "spring", stiffness: 180, damping: 18 },
                          y: { type: "spring", stiffness: 180, damping: 18 },
                          scale: { type: "spring", stiffness: 160, damping: 18 },
                          opacity: { duration: 1.2, repeat: Infinity, ease: "easeInOut" },
                        }}
                      />
                    ))}
                  </div>
                </motion.div>
              </div>
            </div>

            {/* copy */}
            <div className="relative mt-5 text-center">
              <AnimatePresence mode="wait">
                <motion.div
                  key={stageIndex}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={{ duration: 0.22 }}
                >
                  <div className="text-base font-semibold text-white">{stage.title}</div>
                  <div className="mt-1 text-xs text-white/70">{stage.sub}</div>
                </motion.div>
              </AnimatePresence>

              <div className="mt-3 text-[11px] text-white/55">
                Please don’t leave the page while we set it up.
              </div>
            </div>

            {/* footer hint */}
            <div className="relative mt-5 flex items-center justify-between gap-3">
              <div className="text-[11px] text-white/50">This usually takes a few seconds.</div>
              <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-semibold text-white/80">
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-400" />
                Live
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function MatchBlockers({
  status,
  align = "left",
}: {
  status: MatchStatus | null;
  align?: "left" | "center";
}) {
  if (!status) return null;

  const checks = (status as any).checks as
    | {
        verified: boolean;
        assessment_completed: boolean;
        has_interest: boolean;
        has_photos: boolean;
        photos_count?: number;
        not_banned: boolean;
      }
    | undefined;

  const missing = ((status as any).missing ?? []) as string[];
  const reason = (status as any).reason ?? null;

  const issues: string[] = [];

  if (checks) {
    if (!checks.not_banned) issues.push("Your account is banned.");
    if (!checks.verified) issues.push("Your account is not verified.");
    if (!checks.assessment_completed)
      issues.push("Your compatibility assessment is not complete.");
    if (!checks.has_interest) issues.push("Add at least 1 interest to your profile.");
    if (!checks.has_photos) {
      const n = checks.photos_count ?? 0;
      issues.push(`Upload at least 3 gallery photos. (${n}/3)`);
    }
  } else {
    if (missing.length) issues.push(...missing);
    else if (reason) issues.push(reason);
  }

  const show = issues.length > 0 || (!!reason && !status.eligible);
  if (!show) return null;

  const alignClasses =
    align === "center" ? "items-center text-center" : "items-start text-left";
  const buttonsJustify = align === "center" ? "justify-center" : "";

  const needsProfile = checks
    ? !checks.not_banned || !checks.verified || !checks.has_interest || !checks.has_photos
    : issues.some((x) => {
        const s = x.toLowerCase();
        return (
          s.includes("interest") ||
          s.includes("photo") ||
          s.includes("verified") ||
          s.includes("banned")
        );
      });

  const needsAssessment = checks
    ? !checks.assessment_completed
    : issues.some((x) => x.toLowerCase().includes("assessment"));

  return (
    <div
      className={`mt-1 flex flex-col gap-1 rounded-xl bg-rose-50/70 p-2.5 text-[11px] text-rose-700 dark:bg-rose-950/40 dark:text-rose-200 ${alignClasses}`}
    >
      {issues.length > 0 && (
        <ul className="list-disc pl-4 marker:text-rose-400 dark:marker:text-rose-300">
          {issues.map((txt, i) => (
            <li key={i}>{txt}</li>
          ))}
        </ul>
      )}

      {(needsProfile || needsAssessment) && (
        <div className={`mt-1 flex flex-wrap gap-2 ${buttonsJustify}`}>
          {(needsProfile || needsAssessment) && (
            <Link
              to="/profile"
              className="inline-flex items-center gap-1 rounded-full border border-rose-300 bg-white/80 px-2.5 py-0.5 text-[10px] font-medium text-rose-700 hover:bg-rose-50 dark:border-rose-500/60 dark:bg-rose-950/60 dark:text-rose-100"
              title="Fix requirements"
            >
              <ShieldCheck className="h-3 w-3" />
              <span>Fix profile</span>
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

function NoMatchBanner() {
  return (
    <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50/80 p-3 text-xs text-amber-900 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-100">
      <div className="flex items-start gap-2">
        <div className="mt-0.5 grid h-7 w-7 place-items-center rounded-xl bg-white/70 shadow-sm dark:bg-slate-900/60">
          <Search className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <div className="font-semibold">No compatible matches right now</div>
          <div className="mt-0.5 text-[11px] text-amber-700 dark:text-amber-200/80">
            Try again later, or broaden preferences in Profile to increase results.
          </div>
        </div>
      </div>
    </div>
  );
}

function extractIntroPrompts(text?: string) {
  if (!text) return [];
  const lines = text.split("\n").map((l) => l.trim());
  const idx = lines.findIndex((l) => l.toLowerCase().includes("easy ways to start"));
  if (idx === -1) return [];
  const after = lines.slice(idx + 1);
  const prompts = after
    .filter((l) => l.startsWith("•"))
    .map((l) => l.replace(/^•\s*/, "").replace(/\*\*/g, "").trim())
    .filter(Boolean);
  return prompts.slice(0, 3);
}

/** ---------------- Helpers ---------------- */
function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

function msToClock(ms: number) {
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

// ✅ admin-style timestamp display (same spirit as AdminUserJourneyPage fmtTs)
function fmtTs(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString();
}

// compact timestamp for message bubbles
function fmtMsgTime(tsSeconds?: number) {
  if (!tsSeconds) return "";
  const d = new Date(tsSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function pickMatchStartedMsFromConversation(c: any): number | null {
  const candidates = [
    c?.match_started_at_utc,
    c?.matched_at_utc,
    c?.started_at_utc,
    c?.created_at,
    c?.created_at_utc,
  ].filter(Boolean);

  for (const iso of candidates) {
    const ms = Date.parse(String(iso));
    if (!Number.isNaN(ms)) return ms;
  }
  return null;
}

/** ---------------- Page ---------------- */
export default function ConversationsPage() {
  // Widen message type locally without forcing lib/api changes.
  type ThreadMsg = Msg & {
    role: "me" | "them" | "system";
    kind?: string;
    status?: "pending" | "sent" | "read";
  };

  // ✅ widen conversation type locally (so we can read partnerLastActive without touching lib/api)
  type ConversationRow = Conversation & {
    partnerLastActive?: string | null;
    partnerAge?: number | null;
    partnerCity?: string | null;
  };

  const [convos, setConvos] = useState<ConversationRow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadMsg[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [search, setSearch] = useState("");

  const [menuFor, setMenuFor] = useState<string | null>(null);

  const [meId, setMeId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);

  const [reportOpen, setReportOpen] = useState<null | { cid: string; partnerName?: string }>(
    null
  );
  const [reportReason, setReportReason] = useState<ReportReason | "">("");
  const [reportDetails, setReportDetails] = useState("");
  const [reportSaving, setReportSaving] = useState(false);

  // ✅ prevents openConversation race (fast clicks)
  const openSeq = useRef(0);

  useEffect(() => {
    (async () => {
      const [{ data: u }, { data: s }] = await Promise.all([
        supabase.auth.getUser(),
        supabase.auth.getSession(),
      ]);
      setMeId(u.user?.id ?? null);
      setToken(s.session?.access_token ?? null);
    })().catch(() => {
      setMeId(null);
      setToken(null);
    });
  }, []);

  const [matchStatus, setMatchStatus] = useState<MatchStatus | null>(null);
  const [matchAllowed, setMatchAllowed] = useState<boolean>(false);
  const [nextEligibleAt, setNextEligibleAt] = useState<number | null>(null);
  const [noMatch, setNoMatch] = useState(false);

  const now = useNow(1000);
  const remainingMs = useMemo(() => {
    if (nextEligibleAt == null) return 0;
    return Math.max(0, nextEligibleAt - now);
  }, [nextEligibleAt, now]);

  const activeLimitReached = convos.length >= MAX_ACTIVE_MATCHES;

  const canMatchTimewise = matchAllowed && remainingMs === 0;
  const canMatch = canMatchTimewise && !activeLimitReached;
  const countdown = msToClock(remainingMs);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = (await api.conversations()) as any as ConversationRow[];
        if (alive) setConvos(data);
      } catch (e) {
        console.error(e);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const st: MatchStatus = await api.matchStatus();
        if (!alive) return;

        setMatchStatus(st);
        setMatchAllowed(!!st.eligible);

        if ((st as any).next_eligible_ts_utc) {
          const ms = Date.parse((st as any).next_eligible_ts_utc);
          setNextEligibleAt(Number.isNaN(ms) ? null : ms);
        } else {
          setNextEligibleAt(null);
        }
      } catch (e) {
        console.error(e);
        if (!alive) return;
        setMatchStatus({
          eligible: false,
          reason: "Unable to check eligibility.",
          missing: ["Unable to check eligibility."],
        });
        setMatchAllowed(false);
        setNextEligibleAt(null);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const [matchLoading, setMatchLoading] = useState(false);

  // ✅ modal control (keeps it open smoothly during transitions)
  const [matchModalOpen, setMatchModalOpen] = useState(false);
  const matchStartedAtRef = useRef<number | null>(null);

  // photo unlock prompt + status
  const [showFeedbackPrompt, setShowFeedbackPrompt] = useState(false);
  const [feedbackChoice, setFeedbackChoice] = useState<"" | "yes" | "no">("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);

  // store votes (local UI; tries to read from backend if available)
  const [myVote, setMyVote] = useState<null | boolean>(null);
  const [theirVote, setTheirVote] = useState<null | boolean>(null);

  const [unlockedPhotos, setUnlockedPhotos] = useState<string[] | null>(null);
  const [photoViewerUrl, setPhotoViewerUrl] = useState<string | null>(null);

  // typing / presence UI
  const [partnerTyping, setPartnerTyping] = useState(false);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setPhotoViewerUrl(null);
        if (!reportSaving) setReportOpen(null);
        // do NOT close the match popup with ESC (keeps flow)
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [reportSaving]);

  // ✅ WS event handler (FIXED + typing support)
  const onWsEvent = useCallback(
    (evt: WsEvent) => {
      switch (evt.type) {
        case "message-new": {
          const d = evt.data;

          // ✅ if not for the currently open conversation, DO NOT push into thread
          if (!activeId || d.conversation_id !== activeId) {
            // optional: update previews + unread badge
            setConvos((prev) =>
              prev.map((c) => {
                if (c.id !== d.conversation_id) return c;

                const isFromMe = !!meId && d.sender_user_id === meId;
                const unreadInc = isFromMe ? 0 : 1;

                return {
                  ...c,
                  lastMessage: d.content_text ?? c.lastMessage,
                  lastAt: "now" as any, // your backend returns "now"/"5m"/etc
                  unread: (c.unread ?? 0) + unreadInc,
                };
              })
            );
            return;
          }

          // if meId isn't loaded yet, don't mislabel messages
          if (!meId) return;

          const role: "me" | "them" = d.sender_user_id === meId ? "me" : "them";

          setThread((t) => {
            const alreadyThere = t.some((msg) => (msg as any).id === d.id);
            if (alreadyThere) return t;

            // Replace temp message if it was ours
            if (role === "me") {
              const idx = [...t]
                .reverse()
                .findIndex(
                  (m) =>
                    (m as any).role === "me" &&
                    typeof (m as any).id === "string" &&
                    (m as any).id.startsWith("temp-")
                );

              if (idx !== -1) {
                const realIndex = t.length - 1 - idx;
                const clone = t.slice();
                clone[realIndex] = {
                  id: d.id,
                  role,
                  text: d.content_text ?? undefined,
                  ts: Math.floor(new Date(d.created_at).getTime() / 1000),
                  status: "sent",
                } as any;
                return clone;
              }
            }

            return [
              ...t,
              {
                id: d.id,
                role,
                text: d.content_text ?? undefined,
                ts: Math.floor(new Date(d.created_at).getTime() / 1000),
                status: role === "me" ? "sent" : undefined,
              } as any,
            ];
          });

          // keep preview updated for active convo
          setConvos((prev) =>
            prev.map((c) =>
              c.id === d.conversation_id
                ? {
                    ...c,
                    lastMessage: d.content_text ?? c.lastMessage,
                    lastAt: "now" as any,
                  }
                : c
            )
          );

          // receiving a message implies partner is not typing anymore
          if (role === "them") setPartnerTyping(false);

          break;
        }

        case "photos-unlocked": {
          if (!activeId) break;
          if ((evt as any).conversation_id !== activeId) break;

          api
            .conversationPhotos(activeId)
            .then((urls) => setUnlockedPhotos(urls))
            .catch(() => {});
          break;
        }

        case "ended": {
          setConvos((prev) => prev.filter((c) => c.id !== (evt as any).conversation_id));
          if ((evt as any).conversation_id === activeId) {
            setThread([]);
            setActiveId(null);
            setShowFeedbackPrompt(false);
            setUnlockedPhotos(null);
            setPhotoViewerUrl(null);
            setMyVote(null);
            setTheirVote(null);
            setPartnerTyping(false);
          }
          break;
        }

        // optional typing events from your WS hook/backend
        case "typing": {
          // best-effort shape:
          // evt.data = { conversation_id, user_id, is_typing }
          const d: any = (evt as any).data ?? {};
          const cid = d.conversation_id ?? (evt as any).conversation_id;
          if (!activeId || cid !== activeId) break;
          if (!meId) break;

          if (d.user_id && d.user_id !== meId) {
            setPartnerTyping(!!d.is_typing);
          }
          break;
        }

        // optional read receipts event
        case "read": {
          // best-effort: partner read the thread
          const cid = (evt as any).conversation_id ?? (evt as any).data?.conversation_id;
          if (!activeId || cid !== activeId) break;

          setThread((t) =>
            t.map((m) => {
              if ((m as any).role !== "me") return m;
              if ((m as any).status === "pending") return m;
              return { ...(m as any), status: "read" };
            }) as any
          );
          break;
        }

        default:
          break;
      }
    },
    [meId, activeId]
  );

  const { status: wsStatus, sendText, setTyping, markRead: markReadWS } = useConversationWS({
    apiBase: import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000",
    cid: activeId,
    token,
    onEvent: onWsEvent,
  });

  // ✅ race-safe conversation open
  async function openConversation(id: string) {
    const seq = ++openSeq.current;

    setActiveId(id);
    setMenuFor(null);

    setShowFeedbackPrompt(false);
    setFeedbackChoice("");
    setUnlockedPhotos(null);
    setPhotoViewerUrl(null);
    setMyVote(null);
    setTheirVote(null);
    setPartnerTyping(false);

    api.trackEvent({ event_name: "match_opened", conversation_id: id }).catch(() => {});

    const msgs = await api.thread(id);

    // ✅ if user opened a different convo while awaiting, ignore
    if (openSeq.current !== seq) return;

    // mark pending on temp items if any
    setThread((msgs as any[]).map((m) => ({ ...(m as any) })) as any);

    try {
      markReadWS();
    } catch {}
    api.markRead(id).catch(() => {});

    // clear unread for this convo locally
    setConvos((prev) => prev.map((c) => (c.id === id ? { ...c, unread: 0 } : c)));

    try {
      const st = await api.matchFeedbackStatus(id);
      if (openSeq.current !== seq) return;

      setShowFeedbackPrompt(!!(st as any).should_prompt);

      // best-effort vote fields from backend (if present)
      const mine = (st as any).my_vote ?? (st as any).my_answer ?? (st as any).me;
      const theirs = (st as any).partner_vote ?? (st as any).partner_answer ?? (st as any).them;
      if (typeof mine === "boolean") setMyVote(mine);
      if (typeof theirs === "boolean") setTheirVote(theirs);
    } catch {
      if (openSeq.current !== seq) return;
      setShowFeedbackPrompt(false);
    }

    try {
      const urls = await api.conversationPhotos(id);
      if (openSeq.current !== seq) return;
      setUnlockedPhotos(urls);
    } catch {
      if (openSeq.current !== seq) return;
      setUnlockedPhotos(null);
    }
  }

  async function submitFeedback() {
    if (!activeId || feedbackChoice === "") return;
    setFeedbackSaving(true);
    try {
      const vote = feedbackChoice === "yes";
      const res = await api.submitMatchFeedback(activeId, vote);

      setMyVote(vote);
      // best-effort from response
      if (typeof (res as any)?.partner_vote === "boolean") setTheirVote((res as any).partner_vote);

      setShowFeedbackPrompt(false);

      if ((res as any).photos_unlocked) {
        const urls = await api.conversationPhotos(activeId);
        setUnlockedPhotos(urls);
      }
    } catch (e) {
      console.error(e);
      alert("Could not save your answer. Try again.");
    } finally {
      setFeedbackSaving(false);
    }
  }

  // ✅ send (kept your logic, now safe because sockets won’t multiply)
  async function send() {
    if (!message.trim() || !activeId || sending) return;
    setSending(true);

    const text = message.trim();
    const tempId = "temp-" + crypto.randomUUID();

    setThread((t) => [
      ...t,
      {
        id: tempId,
        role: "me",
        text,
        ts: Math.floor(Date.now() / 1000),
        status: "pending",
      } as any,
    ]);

    setMessage("");

    api
      .trackEvent({
        event_name: "message_sent",
        conversation_id: activeId,
        properties: { message_type: "text" },
      })
      .catch(() => {});

    try {
      if (wsStatus === "connected") {
        sendText(text);
      } else {
        await api.sendMessage(activeId, text);
      }
    } finally {
      setSending(false);
    }
  }

  async function endChat(cid: string) {
    try {
      await api.endConversation(cid);

      setConvos((prev) => prev.filter((c) => c.id !== cid));
      setThread((t) => (activeId === cid ? [] : t));
      setActiveId((id) => (id === cid ? null : id));
      setShowFeedbackPrompt(false);
      setUnlockedPhotos(null);
      setPhotoViewerUrl(null);
      setMyVote(null);
      setTheirVote(null);
      setPartnerTyping(false);
    } catch (err) {
      console.error("Failed to end chat", err);
      alert("Couldn't end this chat. Please try again.");
    } finally {
      setMenuFor(null);
    }
  }

  async function refreshMatchStatus() {
    try {
      const st: MatchStatus = await api.matchStatus();
      setMatchStatus(st);
      setMatchAllowed(!!st.eligible);
      if ((st as any).next_eligible_ts_utc) {
        const ms = Date.parse((st as any).next_eligible_ts_utc);
        setNextEligibleAt(Number.isNaN(ms) ? null : ms);
      } else {
        setNextEligibleAt(null);
      }
    } catch {
      // ignore
    }
  }

  async function getNewMatch() {
    if (matchLoading) return;
    if (activeLimitReached) return;

    // open premium popup immediately
    matchStartedAtRef.current = Date.now();
    setMatchModalOpen(true);

    setMatchLoading(true);

    try {
      const res: MatchResponse = await api.match();

      if ((res as any).status === "matched") {
        setNoMatch(false);

        const updated = (await api.conversations()) as any as ConversationRow[];
        setConvos(updated);

        if ((res as any).conversation_id) {
          setActiveId((res as any).conversation_id);
          const msgs = await api.thread((res as any).conversation_id);
          setThread(msgs as any);

          api
            .trackEvent({ event_name: "match_opened", conversation_id: (res as any).conversation_id })
            .catch(() => {});

          try {
            const st = await api.matchFeedbackStatus((res as any).conversation_id);
            setShowFeedbackPrompt(!!(st as any).should_prompt);

            const mine = (st as any).my_vote ?? (st as any).my_answer ?? (st as any).me;
            const theirs = (st as any).partner_vote ?? (st as any).partner_answer ?? (st as any).them;
            setMyVote(typeof mine === "boolean" ? mine : null);
            setTheirVote(typeof theirs === "boolean" ? theirs : null);
          } catch {
            setShowFeedbackPrompt(false);
          }

          try {
            const urls = await api.conversationPhotos((res as any).conversation_id);
            setUnlockedPhotos(urls);
          } catch {
            setUnlockedPhotos(null);
          }
        }

        await refreshMatchStatus();
      } else if ((res as any).status === "ineligible") {
        setNoMatch(false);
        await refreshMatchStatus();
      } else if ((res as any).status === "no_match") {
        setNoMatch(true);
        setMatchStatus((prev) => ({
          ...(prev || { eligible: true }),
          reason: "No compatible match found right now.",
          missing: [],
        }));
      } else {
        setNoMatch(false);
        setMatchStatus((prev) => ({
          ...(prev || { eligible: false }),
          eligible: false,
          reason: "Unexpected response.",
          missing: ["Unexpected response."],
        }));
      }
    } catch (err) {
      setNoMatch(false);
      console.error(err);
      setMatchStatus((prev) => ({
        ...(prev || { eligible: false }),
        eligible: false,
        reason: "Something went wrong.",
        missing: ["Something went wrong."],
      }));
    } finally {
      setMatchLoading(false);

      // small delay so the exit animation feels intentional (not a hard cut)
      window.setTimeout(() => {
        setMatchModalOpen(false);
        matchStartedAtRef.current = null;
      }, 260);
    }
  }

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return convos;
    return convos.filter((c) =>
      [c.partnerName, c.partnerCity, c.lastMessage].some((v) => v?.toLowerCase().includes(q))
    );
  }, [search, convos]);

  const active = convos.find((c) => c.id === activeId) || null;
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), [thread, activeId]);

  const hasAnyConvos = convos.length > 0;

  const hasBlockingReasons = !canMatchTimewise || (matchStatus && !matchStatus.eligible);
  const showTopMatchButton = !hasBlockingReasons;

  const showCuratingModal = matchModalOpen || matchLoading;

  // presence line (typing > online-ish > last active)
  const partnerLastIso = active?.partnerLastActive ?? null;
  const partnerLastMs = partnerLastIso ? Date.parse(partnerLastIso) : NaN;
  const partnerOnlineish =
    !Number.isNaN(partnerLastMs) && Date.now() - partnerLastMs < 2 * 60 * 1000;

  const presenceLine = partnerTyping
    ? "Typing now…"
    : partnerOnlineish
    ? "Active now"
    : partnerLastIso
    ? `Last active: ${fmtTs(partnerLastIso)}`
    : "—";

  // photo lock countdown (from match start)
  const matchStartedMs = useMemo(() => (active ? pickMatchStartedMsFromConversation(active as any) : null), [active]);
  const photoUnlockAt = matchStartedMs != null ? matchStartedMs + PHOTO_LOCK_MS : null;
  const photoRemainingMs = useMemo(() => {
    if (!photoUnlockAt) return 0;
    return Math.max(0, photoUnlockAt - now);
  }, [photoUnlockAt, now]);
  const photoCountdown = msToClock(photoRemainingMs);
  const photoLockActive = !!photoUnlockAt && photoRemainingMs > 0;

  const photoNote = useMemo(() => {
    if (photoUnlockAt == null) return PHOTO_UNLOCK_NOTE_BASE;
    if (photoLockActive) {
      return `${PHOTO_UNLOCK_NOTE_BASE} ${PHOTO_UNLOCK_NOTE_CONSENT} (${photoCountdown} left)`;
    }
    return "If both users press Yes and are satisfied with the match, photos will appear now.";
  }, [photoUnlockAt, photoLockActive, photoCountdown]);

  function voteBadge(val: boolean | null) {
    if (val === null) return null;
    return val ? (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200">
        <Check className="h-3 w-3" /> Yes
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-950/30 dark:text-rose-200">
        ✕ No
      </span>
    );
  }

  return (
    <div className="relative min-h-screen bg-gradient-to-b from-slate-50 via-white to-slate-100 text-slate-900 dark:from-slate-950 dark:via-slate-950 dark:to-slate-900 dark:text-slate-50 transition-colors duration-300">
      {/* ✅ Dopamine popup while curating */}
      <MatchFindingModal open={showCuratingModal} startedAt={matchStartedAtRef.current} />

      <div className="pointer-events-none fixed inset-0 -z-10">
        <div className="absolute -top-32 left-0 right-0 mx-auto h-64 w-64 rounded-full bg-[radial-gradient(circle_at_center,_rgba(129,140,248,0.25),_transparent_60%)] blur-3xl" />
        <div className="absolute bottom-0 right-0 h-72 w-72 rounded-full bg-[radial-gradient(circle_at_center,_rgba(244,114,182,0.18),_transparent_60%)] blur-3xl" />
      </div>

      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-4 sm:px-6 lg:px-8">
        <AppHeader />

        <main className="flex-1 py-6">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1.95fr)]">
            {/* FIX: keep panels fixed height + scroll inside */}
            <aside className="flex min-h-[70vh] max-h-[70vh] flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white/80 p-4 shadow-sm shadow-slate-200/60 backdrop-blur dark:border-slate-700 dark:bg-slate-900/80 dark:shadow-black/30">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <MessageSquareHeart className="h-5 w-5 text-indigo-500 dark:text-indigo-300" />
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-50">
                    Conversations
                  </h2>
                </div>
                <div className="text-[11px] text-slate-500 dark:text-slate-400">
                  {convos.length} active
                </div>
              </div>

              <div className="mt-3 flex items-center gap-2">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Search by name, city, message…"
                    className="w-full rounded-2xl border border-slate-200 bg-white/80 px-9 py-2 text-xs text-slate-900 shadow-sm outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-50 dark:placeholder:text-slate-500 dark:focus:border-indigo-400 dark:focus:ring-indigo-900/40"
                  />
                </div>
                <button
                  className="inline-flex items-center gap-1 rounded-2xl border border-slate-200 bg-white/70 px-3 py-2 text-[11px] text-slate-600 hover:border-indigo-400 hover:text-indigo-600 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200 dark:hover:border-indigo-400 dark:hover:text-indigo-200"
                  title="Filters (coming soon)"
                >
                  <Filter className="h-3.5 w-3.5" />
                  Filters
                </button>
              </div>

              <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex flex-1 flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <Clock className="h-4 w-4 text-indigo-500 dark:text-indigo-300" />
                      {activeLimitReached ? (
                        <span>
                          You can only have {MAX_ACTIVE_MATCHES} matches at a time. End one chat to
                          start a new match.
                        </span>
                      ) : canMatchTimewise ? (
                        <span>You’re eligible for a new match now.</span>
                      ) : nextEligibleAt ? (
                        <span>Next match in {countdown}.</span>
                      ) : (
                        <span>Matching is currently unavailable.</span>
                      )}
                    </div>

                    <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                      Limit: {MAX_ACTIVE_MATCHES} active chats.
                    </div>

                    <MatchBlockers status={matchStatus} align="left" />
                    {noMatch && <NoMatchBanner />}
                  </div>

                  {showTopMatchButton && (
                    <button
                      onClick={getNewMatch}
                      disabled={matchLoading || !canMatch}
                      className={
                        "inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-[11px] font-medium " +
                        (matchLoading
                          ? "bg-slate-200 text-slate-500 cursor-wait dark:bg-slate-800 dark:text-slate-400"
                          : !canMatch
                          ? "bg-slate-200 text-slate-500 cursor-not-allowed dark:bg-slate-800 dark:text-slate-500"
                          : "bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 text-white shadow-sm hover:brightness-110")
                      }
                      title={
                        activeLimitReached
                          ? `You already have ${MAX_ACTIVE_MATCHES} active matches`
                          : matchLoading
                          ? "Matching..."
                          : "Find a new match"
                      }
                    >
                      {matchLoading ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Plus className="h-3.5 w-3.5" />
                      )}
                      <span>New match</span>
                    </button>
                  )}
                </div>
              </div>

              <div className="mt-4 flex-1 space-y-1 overflow-y-auto pr-1">
                {filtered.map((c) => {
                  const isActive = activeId === c.id;
                  const isMenuOpen = menuFor === c.id;

                  const partnerLast = c.partnerLastActive ? fmtTs(c.partnerLastActive) : null;

                  return (
                    <div
                      key={c.id}
                      className={`flex items-start gap-2 rounded-2xl px-2 py-2 transition ${
                        isActive
                          ? "bg-indigo-50/80 ring-1 ring-indigo-200 dark:bg-indigo-950/40 dark:ring-indigo-800/60"
                          : "hover:bg-slate-50/80 dark:hover:bg-slate-800/60"
                      }`}
                    >
                      <button
                        onClick={() => openConversation(c.id)}
                        className="flex flex-1 items-start gap-3 text-left"
                      >
                        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-tr from-indigo-500 via-violet-500 to-rose-500 text-xs font-semibold text-white">
                          {c.partnerName?.substring(0, 1) ?? "?"}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-50">
                              {c.partnerName}
                              {c.partnerAge ? (
                                <span className="text-xs font-normal text-slate-500 dark:text-slate-400">
                                  {" "}
                                  · {c.partnerAge}
                                </span>
                              ) : null}
                              {c.partnerCity ? (
                                <span className="text-xs font-normal text-slate-500 dark:text-slate-400">
                                  {" "}
                                  · {c.partnerCity}
                                </span>
                              ) : null}
                            </div>
                            <div className="shrink-0 text-[11px] text-slate-400 dark:text-slate-500">
                              {c.lastAt}
                            </div>
                          </div>

                          <div className="mt-0.5 truncate text-[11px] text-slate-500 dark:text-slate-400">
                            {partnerLast ? `Last active: ${partnerLast}` : "Last active: —"}
                          </div>

                          <div className="truncate text-[11px] text-slate-600 dark:text-slate-300">
                            {c.lastMessage}
                          </div>

                          <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                            {typeof c.compatibility === "number" && (
                              <div className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-700 dark:bg-slate-800 dark:text-slate-200">
                                <Star className="h-3 w-3 text-rose-500" />
                                <span>{Math.round((c.compatibility || 0) * 100)}%</span>
                              </div>
                            )}
                            {c.unread ? (
                              <span className="ml-auto inline-flex rounded-full bg-rose-600 px-2 py-0.5 text-[10px] font-semibold text-white">
                                {c.unread}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </button>

                      {isActive && (
                        <div className="relative">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setMenuFor(isMenuOpen ? null : c.id);
                            }}
                            className="rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-500 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                            title="More options"
                          >
                            <MoreVertical className="h-4 w-4" />
                          </button>

                          {isMenuOpen && (
                            <div className="absolute right-0 mt-1 w-40 rounded-2xl border border-slate-200 bg-white/95 text-xs text-slate-700 shadow-lg dark:border-slate-700 dark:bg-slate-900/95 dark:text-slate-200">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setMenuFor(null);
                                  setReportOpen({ cid: c.id, partnerName: c.partnerName });
                                  setReportReason("");
                                  setReportDetails("");
                                }}
                                className="w-full rounded-2xl px-4 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60"
                              >
                                Report
                              </button>

                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  endChat(c.id);
                                }}
                                className="w-full rounded-2xl px-4 py-2 text-left text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-950/40"
                              >
                                End chat
                              </button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}

                {filtered.length === 0 && (
                  <div className="flex flex-col items-center px-4 py-8 text-center text-xs text-slate-500 dark:text-slate-400">
                    <MessageSquareHeart className="mb-2 h-6 w-6 text-indigo-500 dark:text-indigo-300" />
                    <div className="text-sm text-slate-700 dark:text-slate-200">
                      No conversations yet.
                    </div>

                    <div className="mt-3">
                      <button
                        onClick={getNewMatch}
                        disabled={matchLoading || !canMatch}
                        className={
                          "inline-flex items-center gap-2 rounded-full px-3 py-2 text-xs font-medium " +
                          (matchLoading
                            ? "bg-slate-200 text-slate-500 cursor-wait dark:bg-slate-800 dark:text-slate-400"
                            : !canMatch
                            ? "bg-slate-200 text-slate-500 cursor-not-allowed dark:bg-slate-800 dark:text-slate-500"
                            : "bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 text-white shadow-sm hover:brightness-110")
                        }
                        title={
                          activeLimitReached
                            ? `You already have ${MAX_ACTIVE_MATCHES} active matches`
                            : undefined
                        }
                      >
                        {matchLoading ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Plus className="h-3.5 w-3.5" />
                        )}
                        <span>Find my first match</span>
                      </button>
                    </div>

                    <div className="mt-2 w-full max-w-xs">
                      <MatchBlockers status={matchStatus} align="center" />
                      {noMatch && <NoMatchBanner />}
                    </div>
                  </div>
                )}
              </div>
            </aside>

            {/* FIX: keep chat panel fixed height + scroll inside */}
            <section className="flex min-h-[70vh] max-h-[70vh] flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white/80 shadow-sm shadow-slate-200/60 backdrop-blur dark:border-slate-700 dark:bg-slate-900/80 dark:shadow-black/30">
              {!active ? (
                <div className="flex-1 p-8">
                  <div className="grid h-full place-items-center">
                    {hasAnyConvos ? (
                      <div className="w-full max-w-sm text-center">
                        <MessageSquareHeart className="mx-auto h-10 w-10 text-indigo-500 dark:text-indigo-300" />
                        <p className="mt-3 text-sm text-slate-700 dark:text-slate-200">
                          Select a conversation on the left to start chatting.
                        </p>

                        <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50/80 p-4 text-left text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200">
                          <div className="flex flex-col gap-1">
                            <div className="flex items-center gap-2">
                              <Clock className="h-4 w-4 text-indigo-500 dark:text-indigo-300" />
                              {activeLimitReached ? (
                                <span>
                                  You can only have {MAX_ACTIVE_MATCHES} matches at a time. End one chat
                                  to start a new match.
                                </span>
                              ) : canMatchTimewise ? (
                                <span>You can request a new match now.</span>
                              ) : nextEligibleAt ? (
                                <span>Next match in {countdown}.</span>
                              ) : (
                                <span>Matching is currently unavailable.</span>
                              )}
                            </div>

                            <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                              Limit: {MAX_ACTIVE_MATCHES} active chats.
                            </div>

                            <MatchBlockers status={matchStatus} align="left" />
                            {noMatch && <NoMatchBanner />}
                          </div>

                          <button
                            onClick={getNewMatch}
                            disabled={matchLoading || !canMatch}
                            className={
                              "mt-4 inline-flex w-full items-center justify-center gap-2 rounded-full px-3 py-2 text-xs font-medium " +
                              (matchLoading
                                ? "bg-slate-200 text-slate-500 cursor-wait dark:bg-slate-800 dark:text-slate-400"
                                : !canMatch
                                ? "bg-slate-200 text-slate-500 cursor-not-allowed dark:bg-slate-800 dark:text-slate-500"
                                : "bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 text-white shadow-sm hover:brightness-110")
                            }
                            title={
                              activeLimitReached
                                ? `You already have ${MAX_ACTIVE_MATCHES} active matches`
                                : undefined
                            }
                          >
                            {matchLoading ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Plus className="h-3.5 w-3.5" />
                            )}
                            <span>New match</span>
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="text-center text-sm text-slate-600 dark:text-slate-300">
                        <MessageSquareHeart className="mx-auto h-10 w-10 text-indigo-500 dark:text-indigo-300" />
                        <p className="mt-3">No conversations yet. Try creating your first match.</p>
                        <div className="mt-4 w-full max-w-xs mx-auto">
                          <MatchBlockers status={matchStatus} align="center" />
                          {noMatch && <NoMatchBanner />}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex items-center justify-between border-b border-slate-200 bg-white/80 px-4 py-3 text-sm dark:border-slate-700 dark:bg-slate-900/80">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-slate-900 dark:text-slate-50">
                          {active?.partnerName}
                        </span>

                        {/* votes next to name */}
                        {voteBadge(myVote)}
                        {voteBadge(theirVote)}

                        {typeof (active as any)?.compatibility === "number" && (
                          <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:bg-rose-950/40 dark:text-rose-200">
                            {Math.round(((active as any).compatibility || 0) * 100)}% match
                          </span>
                        )}
                      </div>

                      {/* Replace “daily match…” line: show age + location + presence */}
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                        {(active as any)?.partnerAge ? <span>{(active as any).partnerAge}</span> : null}
                        {(active as any)?.partnerCity ? (
                          <span>
                            {(active as any).partnerAge ? "· " : ""}
                            {(active as any).partnerCity}
                          </span>
                        ) : null}

                        <span className="opacity-60">
                          {((active as any)?.partnerAge || (active as any)?.partnerCity) ? " · " : ""}
                          {presenceLine}
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 text-[11px] text-slate-500 dark:text-slate-400">
                      <Link
                        to="/profile"
                        className="underline decoration-slate-300 underline-offset-4 hover:text-indigo-600 dark:decoration-slate-600 dark:hover:text-indigo-300"
                      >
                        Adjust preferences
                      </Link>
                      <ArrowRight className="h-3.5 w-3.5" />
                    </div>
                  </div>

                  {showFeedbackPrompt && (
                    <div className="mx-4 mt-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-3 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200">
                      <div className="font-medium text-slate-900 dark:text-slate-50">
                        Are you satisfied with this match?
                      </div>

                      <div className="mt-2 flex items-center gap-2">
                        <select
                          value={feedbackChoice}
                          onChange={(e) => setFeedbackChoice(e.target.value as any)}
                          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-900"
                        >
                          <option value="">Select…</option>
                          <option value="yes">Yes</option>
                          <option value="no">No</option>
                        </select>

                        <button
                          type="button"
                          onClick={submitFeedback}
                          disabled={feedbackSaving || feedbackChoice === ""}
                          className="rounded-xl bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 px-3 py-2 text-xs font-semibold text-white disabled:opacity-60"
                        >
                          {feedbackSaving ? "Saving..." : "Submit"}
                        </button>
                      </div>

                      <div className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
                        {photoNote}
                      </div>
                    </div>
                  )}

                  {active && (
                    <>
                      {unlockedPhotos && unlockedPhotos.length > 0 ? (
                        <div className="mx-4 mt-3 flex gap-2 overflow-x-auto pb-1">
                          {unlockedPhotos.map((u) => (
                            <button
                              key={u}
                              type="button"
                              onClick={() => setPhotoViewerUrl(u)}
                              className="shrink-0 rounded-2xl focus:outline-none focus:ring-2 focus:ring-indigo-400"
                              title="Open photo"
                            >
                              <img
                                src={u}
                                className="h-24 w-24 rounded-2xl object-cover"
                                alt="Unlocked"
                                loading="lazy"
                              />
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="mx-4 mt-3 rounded-2xl border border-slate-200 bg-slate-50/80 p-3 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-200">
                          <div className="flex items-start gap-2">
                            <div className="mt-0.5 grid h-7 w-7 place-items-center rounded-xl bg-white/70 shadow-sm dark:bg-slate-950/60">
                              <ShieldCheck className="h-4 w-4 text-indigo-500 dark:text-indigo-300" />
                            </div>
                            <div className="min-w-0">
                              <div className="font-semibold">Photos locked</div>
                              <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                                {photoNote}
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                    </>
                  )}

                  {/* FIX: message list is fixed-height scrollable */}
                  <div className="flex-1 overflow-y-auto px-4 py-4">
                    <div className="space-y-3">
                      {thread.map((m) => {
                        if ((m as any).role === "system") {
                          const prompts = extractIntroPrompts((m as any).text);
                          return (
                            <motion.div
                              key={(m as any).id}
                              initial={{ opacity: 0, y: 6 }}
                              animate={{ opacity: 1, y: 0 }}
                              className="flex justify-center"
                            >
                              <div className="w-full max-w-[92%] rounded-3xl border border-indigo-200 bg-indigo-50/70 p-4 text-sm text-slate-900 shadow-sm dark:border-indigo-800/50 dark:bg-indigo-950/40 dark:text-slate-50">
                                <div className="flex items-center gap-2 text-[11px] font-semibold text-indigo-700 dark:text-indigo-200">
                                  <Sparkles className="h-4 w-4" />
                                  Match intro
                                </div>

                                <div className="mt-2 whitespace-pre-wrap leading-relaxed text-slate-800 dark:text-slate-100">
                                  {(m as any).text}
                                </div>

                                {prompts.length > 0 && (
                                  <div className="mt-3 flex flex-wrap gap-2">
                                    {prompts.map((p) => (
                                      <button
                                        key={p}
                                        type="button"
                                        onClick={() => setMessage(p)}
                                        className="rounded-full border border-indigo-200 bg-white/70 px-3 py-1 text-[11px] font-medium text-indigo-700 hover:bg-white dark:border-indigo-800/50 dark:bg-slate-900/60 dark:text-indigo-200"
                                        title="Use this starter"
                                      >
                                        {p}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </motion.div>
                          );
                        }

                        const isMe = (m as any).role === "me";
                        const time = fmtMsgTime((m as any).ts);
                        const status = (m as any).status as ThreadMsg["status"] | undefined;

                        return (
                          <motion.div
                            key={(m as any).id}
                            initial={{ opacity: 0, y: 6 }}
                            animate={{ opacity: 1, y: 0 }}
                            className={`flex ${isMe ? "justify-end" : "justify-start"}`}
                          >
                            <div className={`max-w-[78%]`}>
                              <div
                                className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm ${
                                  isMe
                                    ? "bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 text-white"
                                    : "bg-slate-100/90 text-slate-900 dark:bg-slate-800/80 dark:text-slate-50"
                                }`}
                              >
                                {(m as any).text}
                              </div>

                              {/* timestamp + sent/read */}
                              <div
                                className={`mt-1 flex items-center gap-2 text-[11px] ${
                                  isMe ? "justify-end text-slate-400 dark:text-slate-500" : "text-slate-400 dark:text-slate-500"
                                }`}
                              >
                                {time ? <span>{time}</span> : null}

                                {isMe ? (
                                  <span className="inline-flex items-center gap-1">
                                    {status === "pending" ? (
                                      <>
                                        <Clock className="h-3 w-3" />
                                        <span>Sending</span>
                                      </>
                                    ) : status === "read" ? (
                                      <>
                                        <CheckCheck className="h-3 w-3" />
                                        <span>Read</span>
                                      </>
                                    ) : (
                                      <>
                                        <Check className="h-3 w-3" />
                                        <span>Sent</span>
                                      </>
                                    )}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          </motion.div>
                        );
                      })}
                      <div ref={bottomRef} />
                    </div>
                  </div>

                  <form
                    className="flex items-center gap-3 border-t border-slate-200 bg-white/80 px-4 py-3 dark:border-slate-700 dark:bg-slate-900/80"
                    onSubmit={(e) => {
                      e.preventDefault();
                      send();
                    }}
                  >
                    <input
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                      onFocus={() => activeId && (markReadWS(), api.markRead(activeId).catch(() => {}))}
                      placeholder="Write a message…"
                      className="flex-1 rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-50 dark:placeholder:text-slate-500 dark:focus:border-indigo-400 dark:focus:ring-indigo-900/40"
                      onKeyDown={() => setTyping(true)}
                      onBlur={() => setTyping(false)}
                    />
                    <button
                      type="submit"
                      disabled={sending}
                      className="inline-flex items-center gap-2 rounded-2xl bg-gradient-to-r from-indigo-500 via-violet-500 to-rose-500 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:brightness-110 disabled:opacity-60"
                    >
                      {sending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <SendHorizontal className="h-4 w-4" />
                      )}
                      Send
                    </button>
                  </form>

                  <div className="px-4 pb-3 text-[11px] text-slate-400 dark:text-slate-500">
                    {partnerTyping ? "Typing now…" : ""}{" "}
                    <span className="opacity-60">WS: {wsStatus}</span>
                  </div>
                </>
              )}
            </section>
          </div>
        </main>

        <AppFooter />
      </div>

      {/* REPORT MODAL (does NOT end chat) */}
      {reportOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          onClick={() => !reportSaving && setReportOpen(null)}
        >
          <div
            className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-4 shadow-xl dark:border-slate-700 dark:bg-slate-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="text-sm font-semibold">Report {reportOpen.partnerName || "user"}</div>
              <button
                type="button"
                onClick={() => !reportSaving && setReportOpen(null)}
                className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-900 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-50 dark:hover:bg-slate-700"
              >
                ✕
              </button>
            </div>

            <div className="mt-3 text-xs text-slate-600 dark:text-slate-300">Reason</div>
            <select
              value={reportReason}
              onChange={(e) => setReportReason(e.target.value as ReportReason)}
              className="mt-1 w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
              disabled={reportSaving}
            >
              <option value="">Select…</option>
              <option value="spam">Spam</option>
              <option value="harassment">Harassment</option>
              <option value="scam">Scam / fraud</option>
              <option value="hate">Hate</option>
              <option value="sexual">Sexual content</option>
              <option value="impersonation">Impersonation</option>
              <option value="other">Other</option>
            </select>

            <div className="mt-3 text-xs text-slate-600 dark:text-slate-300">Details (optional)</div>
            <textarea
              value={reportDetails}
              onChange={(e) => setReportDetails(e.target.value)}
              rows={4}
              className="mt-1 w-full resize-none rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
              placeholder="What happened?"
              disabled={reportSaving}
            />

            <div className="mt-4 flex gap-2">
              <button
                type="button"
                onClick={() => setReportOpen(null)}
                disabled={reportSaving}
                className="flex-1 rounded-2xl border border-slate-200 px-3 py-2 text-sm dark:border-slate-700"
              >
                Cancel
              </button>

              <button
                type="button"
                disabled={reportSaving || reportReason === ""}
                onClick={async () => {
                  if (!reportOpen || reportReason === "") return;
                  setReportSaving(true);
                  try {
                    await api.reportUser({
                      conversation_id: reportOpen.cid,
                      reason: reportReason,
                      details: reportDetails || undefined,
                    });

                    setReportOpen(null);
                    setMenuFor(null);
                    alert("Report submitted.");
                  } catch (e) {
                    console.error(e);
                    alert("Could not submit report. Try again.");
                  } finally {
                    setReportSaving(false);
                  }
                }}
                className="flex-1 rounded-2xl bg-indigo-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60"
              >
                {reportSaving ? "Submitting..." : "Submit report"}
              </button>
            </div>
          </div>
        </div>
      )}

      {photoViewerUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
          onClick={() => setPhotoViewerUrl(null)}
        >
          <div className="relative max-h-[85vh] max-w-[92vw]" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              onClick={() => setPhotoViewerUrl(null)}
              className="absolute -right-3 -top-3 rounded-full bg-white/90 px-3 py-1 text-xs font-semibold text-slate-900 shadow hover:bg-white"
              aria-label="Close photo"
            >
              ✕
            </button>

            <img
              src={photoViewerUrl}
              alt="Opened photo"
              className="max-h-[85vh] max-w-[92vw] rounded-3xl object-contain shadow-2xl"
            />
          </div>
        </div>
      )}
    </div>
  );
}
