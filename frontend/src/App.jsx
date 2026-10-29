import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Award, Bell, BookOpen, CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, Clipboard, Clock3,
  Coins, Compass, Flame, Home, LayoutGrid, List, LockKeyhole, LogOut, MessageCircle, Moon,
  MoreHorizontal, Music, Pencil, Plus, Search, Send, ShieldCheck, Sparkles, Star, Sun,
  Target, Trophy, Users, Video, X, Zap
} from "lucide-react";
import logoMark from "./assets/gottime-logo.svg";
import { guardianNotice, meetingUrl, nextLearnerQuiz, sessionChatContact } from "./sessionRules";
import { createChatCrypto } from "./chatCrypto";
import { createCheckoutFeedback } from "./checkoutFeedback";
import { quizOptionState } from "./quizResults";
import { checkBookingAvailability } from "./bookingAvailability";
import DonationUpiPanel from "./DonationUpiPanel";
import { razorpayPaymentOptions } from "./razorpayCheckout";
import { refreshProfileStats, formatRating } from "./profileRatings";
import { createMissedSessionNotices } from "./missedSessionNotices";
import { questDay, questIsCurrent, questCount as dailyQuestCount, questResetDelay } from "./dailyQuest";

const navItems = [
  { id: "home", label: "Home", icon: Home },
  { id: "discover", label: "Discover", icon: Compass },
  { id: "sessions", label: "My sessions", icon: CalendarDays },
  { id: "calendar", label: "Calendar", icon: Clock3 },
  { id: "skills", label: "My skills", icon: BookOpen },
  { id: "badges", label: "Badge vault", icon: Award },
  { id: "messages", label: "Messages", icon: MessageCircle },
  { id: "leaderboard", label: "Leaderboard", icon: Trophy },
  { id: "admin", label: "Admin", icon: ShieldCheck, adminOnly: true },
];
const appPages = ["home", "discover", "mentor-detail", "sessions", "calendar", "skills", "badges", "messages", "notifications", "leaderboard", "profile", "subscription", "donation", "testimonials", "about", "admin"];
const AUTH_TOKEN_KEY = "gt-auth-token-v2";
const gradients = ["mint", "violet", "peach", "blue", "yellow"];
const bannerOptions = [
  { id: "mint", label: "Calm green" },
  { id: "blue", label: "Sky blue" },
  { id: "violet", label: "Soft violet" },
  { id: "peach", label: "Warm peach" },
  { id: "yellow", label: "Sun glow" },
];
const categoryEmoji = { Programming: "</>", Design: "UI", Communication: "Chat", Music: "Music", Business: "Biz", Wellness: "Well", Language: "Lang", Other: "New" };
const loadingQuotes = [
  "A good hour can change the whole week.",
  "Trade time. Keep the curiosity.",
  "Someone knows the thing you want to learn.",
  "Small skills compound into quiet superpowers.",
  "Learning feels better when a real person shows up.",
  "Your next spark might be one conversation away.",
];

function randomQuote(seed = Date.now()) {
  return loadingQuotes[Math.abs(Number(seed) || 0) % loadingQuotes.length];
}

function modeLabel(mode = "online") {
  const clean = String(mode || "online").toLowerCase();
  if (clean === "offline") return "Offline";
  if (clean === "both") return "Online / Offline";
  return "Online";
}

async function api(path, options = {}) {
  const authToken = sessionStorage.getItem(AUTH_TOKEN_KEY);
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(authToken ? { "X-GotTime-Auth": authToken } : {}), ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || "Something went sideways.");
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function loadExternalScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve(true);
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve(true);
    script.onerror = () => reject(new Error("Could not load payment checkout."));
    document.body.appendChild(script);
  });
}
function storeAuthToken(token) {
  localStorage.removeItem("gt-auth-token");
  sessionStorage.removeItem("gt-auth-token");
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  if (token) sessionStorage.setItem(AUTH_TOKEN_KEY, token);
}
function clearAuthToken() {
  sessionStorage.removeItem("gt-auth-token");
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem("gt-auth-token");
}
function currentAuthToken() {
  return sessionStorage.getItem(AUTH_TOKEN_KEY);
}
const { getMessageKeys, decryptMessage, buildEncryptedMessage, buildRecoveryCopy } = createChatCrypto({
  crypto: window.crypto, storage: localStorage, locks: navigator.locks,
});
async function ensureMessageKey(userId) {
  if (!window.crypto?.subtle || !userId) return null;
  const keys = await getMessageKeys(userId);
  await api("/api/messages/public-key", { method: "POST", body: JSON.stringify({ publicKey: keys.publicJwk }) });
  return keys;
}
async function decryptForMe(message, myUserId) {
  try {
    return { content: await decryptMessage(message, myUserId), decryptionFailed: false };
  } catch {
    return { content: "Waiting for message recovery. Open this chat on the browser where the old messages are readable.", decryptionFailed: true };
  }
}
async function recoverReadableMessages(messages, userId, contactId) {
  const { publicKey } = await api(`/api/messages/key/${contactId}`);
  if (!publicKey) return;
  for (const message of messages) {
    try {
      const recoveryCopy = await buildRecoveryCopy(message, userId, contactId, publicKey);
      if (recoveryCopy) await api(`/api/messages/item/${message.id}/recover`, {
        method: "POST", body: JSON.stringify({ recoveryCopy, expectedEncryptedContent: message.encrypted_content }),
      });
    } catch { /* Unreadable messages are never rewritten; conflicts retry on the next poll. */ }
  }
}
async function encryptLegacyThread(messages, myUserId, contactId) {
  const legacy = (messages || []).filter((message) => !message.encrypted_content && message.content && message.content !== "[encrypted message]");
  if (!legacy.length) return 0;
  await ensureMessageKey(myUserId);
  const keyResult = await api(`/api/messages/key/${contactId}`);
  if (!keyResult.publicKey) return 0;
  let encryptedCount = 0;
  for (const message of legacy) {
    try {
      const encryptedContent = await buildEncryptedMessage({ senderId: myUserId, receiverId: contactId, receiverPublicKey: keyResult.publicKey, content: message.content });
      if (!encryptedContent) continue;
      await api(`/api/messages/item/${message.id}/encrypt`, { method: "POST", body: JSON.stringify({ encryptedContent }) });
      encryptedCount += 1;
    } catch (error) {
      console.warn("Could not encrypt legacy message", message.id, error);
    }
  }
  return encryptedCount;
}
function initials(name = "GT") { return name.split(" ").map((p) => p[0]).join("").slice(0, 2).toUpperCase(); }
function bannerImageUrl(file) { return file ? `/static/uploads/${file}` : ""; }
function parseScheduledDate(value) {
  if (!value) return null;
  const text = String(value);
  const direct = text.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (direct) return new Date(Number(direct[1]), Number(direct[2]) - 1, Number(direct[3]), Number(direct[4]), Number(direct[5]), Number(direct[6] || 0));
  const rfc = text.match(/^[A-Za-z]{3},\s+(\d{2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (rfc) {
    const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].indexOf(rfc[2]);
    if (month >= 0) return new Date(Number(rfc[3]), month, Number(rfc[1]), Number(rfc[4]), Number(rfc[5]), Number(rfc[6] || 0));
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}
function formatDate(value, options = {}) {
  if (!value) return "Time to be agreed";
  const date = parseScheduledDate(value);
  return !date ? String(value) : date.toLocaleDateString("en", { month: "short", day: "numeric", ...options });
}
function formatTimeIST(value) {
  const date = parseScheduledDate(value);
  return !date ? String(value || "") : date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true });
}
function formatSessionTime(value) {
  if (!value) return "Time to be agreed";
  const date = parseScheduledDate(value);
  return !date ? String(value) : date.toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}
function memberLabel() { return "Time trader"; }
function cleanBio(bio = "") { const text = String(bio || "").trim(); return text === "Signed up with Google." ? "" : text; }
function mentorSkills(mentor = {}) { return (mentor.skills || "").split(",").map((skill) => skill.trim()).filter(Boolean); }
function mentorPrimarySkill(mentor = {}) { return mentor.primary_skill || mentorSkills(mentor)[0] || "Skill mentoring"; }
function mentorPrimaryDescription(mentor = {}) { return mentor.primary_description || cleanBio(mentor.bio) || "A practical, friendly session built around what the learner needs."; }
function parsePreferredTimeRange(value = "") {
  const match = String(value || "").match(/(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})/);
  return match ? { start: match[1], end: match[2] } : { start: "", end: "" };
}
function minutesFromTime(value = "") {
  const [hours, minutes] = String(value || "").split(":").map(Number);
  return Number.isFinite(hours) && Number.isFinite(minutes) ? hours * 60 + minutes : null;
}
function formatClock(value = "") {
  const mins = minutesFromTime(value);
  if (mins === null) return "";
  const hours = Math.floor(mins / 60);
  const minutes = mins % 60;
  const suffix = hours >= 12 ? "PM" : "AM";
  const displayHour = ((hours + 11) % 12) + 1;
  return `${displayHour}:${String(minutes).padStart(2, "0")} ${suffix}`;
}
function clockPartsFromTime(value = "") {
  const mins = minutesFromTime(value);
  const safeMins = mins === null ? 17 * 60 : mins;
  const hours = Math.floor(safeMins / 60);
  return {
    hour: String(((hours + 11) % 12) + 1),
    minute: String(safeMins % 60).padStart(2, "0"),
    period: hours >= 12 ? "PM" : "AM",
  };
}
function timeFromClockParts(hourValue, minuteValue, period) {
  let hour = Math.max(1, Math.min(12, Number(hourValue) || 12));
  const minute = Math.max(0, Math.min(59, Number(minuteValue) || 0));
  if (period === "PM" && hour < 12) hour += 12;
  if (period === "AM" && hour === 12) hour = 0;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}
function parseTypedClock(value = "") {
  const text = String(value || "").trim().toUpperCase().replace(/\s+/g, " ");
  const match = text.match(/^(\d{1,2})(?::(\d{1,2}))?\s*(AM|PM)?$/);
  if (!match) return "";
  let hour = Number(match[1]);
  const minute = Number(match[2] ?? 0);
  const period = match[3] || "";
  if (!Number.isFinite(hour) || !Number.isFinite(minute) || minute < 0 || minute > 59) return "";
  if (period) {
    if (hour < 1 || hour > 12) return "";
    if (period === "PM" && hour < 12) hour += 12;
    if (period === "AM" && hour === 12) hour = 0;
  } else if (hour < 0 || hour > 23) {
    return "";
  }
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}
function defaultPeriodForRange(start = "", end = "") {
  const startMins = minutesFromTime(start), endMins = minutesFromTime(end);
  if (startMins !== null && endMins !== null && startMins >= 12 * 60 && endMins >= 12 * 60) return "PM";
  if (startMins !== null && endMins !== null && startMins < 12 * 60 && endMins < 12 * 60) return "AM";
  return "PM";
}
function timeOptionsBetween(start = "", end = "") {
  const all = Array.from({ length: 48 }, (_, index) => {
    const mins = index * 30;
    const value = `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
    return { value, label: formatClock(value), minutes: mins };
  });
  const startMins = minutesFromTime(start), endMins = minutesFromTime(end);
  if (startMins === null || endMins === null) return all;
  return all.filter((option) => startMins <= endMins ? option.minutes >= startMins && option.minutes <= endMins : option.minutes >= startMins || option.minutes <= endMins);
}
function preferredTimeLabel(value = "") {
  const range = parsePreferredTimeRange(value);
  return range.start && range.end ? `${formatClock(range.start)} - ${formatClock(range.end)}` : "Flexible";
}
function isDateTimeInsidePreferredRange(dateTimeValue = "", rangeValue = "") {
  const range = parsePreferredTimeRange(rangeValue);
  if (!range.start || !range.end || !dateTimeValue) return true;
  const selected = minutesFromTime(String(dateTimeValue).split("T")[1]?.slice(0, 5));
  const start = minutesFromTime(range.start);
  const end = minutesFromTime(range.end);
  if (selected === null || start === null || end === null) return true;
  return start <= end ? selected >= start && selected <= end : selected >= start || selected <= end;
}
function categoryInitials(value = "") {
  const words = String(value || "Skill").replace(/[^a-z0-9 ]/gi, " ").trim().split(/\s+/).filter(Boolean);
  return (words.length > 1 ? `${words[0][0]}${words[1][0]}` : words[0]?.slice(0, 2) || "SK").toUpperCase();
}
function skillIconFor(category = "", skill = "") {
  const text = `${category} ${skill}`.toLowerCase();
  if (/video|film|edit|camera|photo/.test(text)) return Video;
  if (/music|song|audio|guitar|piano|vocal/.test(text)) return Music;
  if (/program|code|web|app|python|java|react/.test(text)) return BookOpen;
  if (/design|ui|ux|art|draw/.test(text)) return Sparkles;
  if (/communicat|speak|english|language/.test(text)) return MessageCircle;
  if (/business|finance|market|sales/.test(text)) return Coins;
  if (/wellness|fitness|health|yoga/.test(text)) return Sun;
  return null;
}
function MentorCoverIcon({ mentor }) {
  const category = (mentor.categories || "").split(",")[0]?.trim() || "";
  const skill = mentorPrimarySkill(mentor);
  const Icon = skillIconFor(category, skill);
  return Icon ? <Icon size={36} strokeWidth={2.4} /> : <b>{categoryInitials(category === "Other" ? skill : category || skill)}</b>;
}
function SkillCardIcon({ skill }) {
  const name = skill?.name || skill?.skill_name || "";
  const category = skill?.category || "";
  const Icon = skillIconFor(category, name);
  return Icon ? <Icon size={24} strokeWidth={2.4} /> : <b>{categoryInitials(category === "Other" ? name : category || name)}</b>;
}
function photoUrl(photo) { return !photo ? "" : /^https?:\/\//i.test(photo) ? photo : `/static/uploads/${photo}`; }
function buildBadgeVault(data) {
  const earned = new Set((data.badges || []).map((badge) => badge.name));
  const xp = Number(data.user?.xp || 0);
  const sessions = data.sessions || [];
  const learned = sessions.filter((s) => s.status === "completed" && s.learner_id === data.user.id).length;
  const taught = sessions.filter((s) => s.status === "completed" && s.mentor_id === data.user.id).length;
  const completed = learned + taught;
  const skillsShared = (data.mySkills || []).length;
  const chatPeople = (data.contacts || []).filter((contact) => contact.last_message).length;
  const scheduled = sessions.filter((s) => s.scheduled_for && !["cancelled", "missed"].includes(s.status)).length;
  const streak = Number(data.user?.streak || 0);
  const blueprints = [
    { name: "Curiosity Spark", tier: "XP badge", icon: Zap, color: "mint", shape: "burst", target: 50, value: xp, condition: "Reach 50 total XP, including daily quests.", reward: "Spark" },
    { name: "Knowledge Seeker", tier: "XP badge", icon: BookOpen, color: "violet", shape: "book", target: 250, value: xp, condition: "Reach 250 total XP, including daily quests.", reward: "Seeker" },
    { name: "Time Legend", tier: "XP badge", icon: Trophy, color: "yellow", shape: "crown", target: 500, value: xp, condition: "Reach 500 total XP, including daily quests.", reward: "Legend" },
    { name: "First Daily Quest", tier: "Quest badge", icon: Target, color: "peach", shape: "burst", target: 1, value: earned.has("First Daily Quest") ? 1 : 0, condition: "Complete your first daily quest. Awarded once.", reward: "First quest" },
    { name: "First Session Completed", tier: "Session badge", icon: Check, color: "blue", shape: "shield", target: 1, value: learned, condition: "Finish 1 session.", reward: "First" },
    { name: "Skill Hunter", tier: "Learning badge", icon: Compass, color: "violet", shape: "diamond", target: 5, value: learned, condition: "Finish 5 learning sessions.", reward: "Hunter" },
    { name: "First Session Taught", tier: "Teaching badge", icon: Sparkles, color: "peach", shape: "spark", target: 1, value: taught, condition: "Complete your first session as a mentor.", reward: "Mentor" },
    { name: "Guide Master", tier: "Teaching badge", icon: ShieldCheck, color: "yellow", shape: "hex", target: 5, value: taught, condition: "Teach 5 sessions.", reward: "Guide" },
    { name: "Skill Sharer", tier: "Skill badge", icon: Plus, color: "mint", shape: "leaf", target: 1, value: skillsShared, condition: "Add 1 skill.", reward: "Sharer" },
    { name: "Conversation Starter", tier: "Social badge", icon: MessageCircle, color: "violet", shape: "bubble", target: 1, value: chatPeople, condition: "Start 1 mentor chat.", reward: "Chat" },
    { name: "Calendar Captain", tier: "Planning badge", icon: CalendarDays, color: "blue", shape: "calendar", target: 1, value: scheduled, condition: "Schedule 1 session.", reward: "Planner" },
    { name: "Time Trader", tier: "Master badge", icon: Clock3, color: "mint", shape: "hourglass", target: 10, value: completed, condition: "Complete 10 sessions.", reward: "Trader" },
  ];
  return blueprints.map((badge) => ({ ...badge, unlocked: earned.has(badge.name), progress: Math.min(100, Math.round((badge.value / badge.target) * 100)) }));
}
function readPageStack(key) {
  try {
    const stack = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(stack) ? stack.filter((item) => appPages.includes(item)).slice(-12) : [];
  } catch {
    return [];
  }
}
function writePageStack(key, stack) {
  localStorage.setItem(key, JSON.stringify(stack.filter((item) => appPages.includes(item)).slice(-12)));
}
function todayKey() {
  return questDay();
}
function dailyMentorViewsKey(userId, day = todayKey()) {
  return `gt-mentor-views-${userId || "guest"}-${day}`;
}
function readDailyMentorViews(userId, day) {
  try {
    const views = JSON.parse(localStorage.getItem(dailyMentorViewsKey(userId, day)) || "[]");
    return Array.isArray(views) ? views.map(Number).filter(Boolean) : [];
  } catch {
    return [];
  }
}
function writeDailyMentorViews(userId, views, day) {
  localStorage.setItem(dailyMentorViewsKey(userId, day), JSON.stringify([...new Set(views.map(Number).filter(Boolean))]));
}
function fallbackDailyQuest() {
  return { key: "mentor_explorer", title: "Curiosity collector", description: "Explore 2 mentor profiles today.", target: 2, kind: "mentor_views", xp: 20 };
}

function Avatar({ name, size = "md", index = 0, profilePhoto = "" }) {
  const src = photoUrl(profilePhoto);
  return <span className={`avatar avatar-${size} avatar-${gradients[index % gradients.length]}`}>{src ? <img src={src} alt={`${name || "User"} profile`} /> : <span className="avatar-initials">{initials(name)}</span>}</span>;
}
function Logo({ compact = false, onClick, tagline = false }) {
  const Tag = onClick ? "button" : "div";
  return <Tag className={`logo ${onClick ? "logo-button" : ""}`} type={onClick ? "button" : undefined} onClick={onClick}><span className="logo-glyph"><img src={logoMark} alt="" /></span>{!compact && <span className="logo-copy"><span>GotTime<span className="logo-q">?</span></span>{tagline && <small>Trade Hours. Learn Skills.</small>}</span>}</Tag>;
}
function BrandText({ onClick }) {
  const Tag = onClick ? "button" : "div";
  return <Tag className={`brand-text ${onClick ? "logo-button" : ""}`} type={onClick ? "button" : undefined} onClick={onClick}><span>GotTime<span className="logo-q">?</span></span><small>Trade Hours. Learn Skills.</small></Tag>;
}
function SiteFooter({ onNavigate, onAuth }) {
  const go = (page) => onNavigate ? onNavigate(page) : onAuth?.("register");
  return <footer className="site-footer"><div className="footer-main"><div className="footer-brand"><Logo tagline /><p>GotTime helps people trade hours, learn useful skills, and grow through real human mentorship.</p><div className="footer-contact"><span>Email: support@gottime.app</span><span>Phone: +91 98765 43210</span></div></div><div className="footer-links"><section><h4>Explore</h4><button onClick={() => go("discover")}>Browse mentors</button><button onClick={() => go("skills")}>Share skills</button><button onClick={() => go("leaderboard")}>Leaderboard</button><button onClick={() => go("subscription")}>Buy credits</button><button onClick={() => go("donation")}>Donate</button></section><section><h4>Company</h4><button onClick={() => go("about")}>About us</button><a href="mailto:support@gottime.app?subject=GotTime%20contact">Contact us</a><button onClick={() => go("testimonials")}>Feedback</button></section><section><h4>Stories</h4><button onClick={() => go("testimonials")}>Testimonials</button><p>Real wins from learners and mentors.</p></section></div></div><div className="footer-bottom"><span><sup>{"\u00A9"}</sup> {new Date().getFullYear()} GotTime? All rights reserved.</span></div></footer>;
}

function LoadingQuote({ quote }) {
  return <p className="loading-quote">"{quote}"</p>;
}

function GuestLanding({ onAuth }) {
  const [showPrompt, setShowPrompt] = useState(false);
  useEffect(() => { const t = setTimeout(() => setShowPrompt(true), 10000); return () => clearTimeout(t); }, []);
  const requireSignup = () => onAuth("register");
  const lanes = [
    { icon: Coins, title: "Start with 10 credits", text: "Create an account and get credits to try your first sessions." },
    { icon: BookOpen, title: "Use credits to learn", text: "Find a skill, chat with a mentor, and book a session for 1 credit." },
    { icon: Sparkles, title: "Teach to earn more", text: "Share a skill you know. Earn 1 credit for each completed teaching session, then use it to learn." }
  ];
  const mentorCards = [
    { title: "Design critique", tag: "UI / UX", text: "Get feedback on layouts, colors, and portfolio screens.", icon: "UI", color: "pink" },
    { title: "Python coaching", tag: "Code", text: "Debug basics, understand Flask, and practise small tasks.", icon: "</>", color: "blue" },
    { title: "Public speaking", tag: "Confidence", text: "Prepare a talk, rehearse answers, and reduce stage fear.", icon: "Mic", color: "yellow" }
  ];
  return (
    <main className="guest-page">
      <nav className="guest-nav"><Logo /><div><button className="text-button" onClick={() => onAuth("login")}>Sign in</button><button className="primary" onClick={requireSignup}>Get 10 credits</button></div></nav>
      <section className="guest-hero">
        <div className="guest-copy">
          <span className="eyebrow"><Users size={15} /> A community for sharing skills</span>
          <h1>Share a skill.<br />Learn a new one.</h1>
          <p>GotTime connects people who want to learn with people who can teach. Use credits to book a mentor, and earn credits by teaching a skill you know.</p>
          <div className="guest-credit-explainer"><Coins size={21} /><span><strong>Teach → earn credits → learn</strong><small>You can be both a learner and a mentor.</small></span></div>
          <div className="guest-actions"><button className="primary" onClick={requireSignup}>Get started <ArrowRight size={17} /></button><a className="soft-button" href="#guest-how-it-works">How it works <ChevronDown size={17} /></a></div>
          <p className="guest-welcome-note">Join with 10 free credits. No need to teach before you start learning.</p>
        </div>
        <aside className="guest-example" aria-labelledby="guest-example-title">
          <span className="guest-example-label"><MessageCircle size={17} /> A session could start like this</span>
          <div className="guest-example-heading"><span className="guest-example-icon"><Users size={27} /></span><div><h2 id="guest-example-title">Take better photos</h2><p>Photography · Online</p></div></div>
          <div className="guest-example-conversation">
            <div className="guest-example-message"><small>Learner</small><p>Can I learn photography with just my phone?</p></div>
            <div className="guest-example-message mentor-reply"><small>Mentor</small><p>Absolutely! I'll show you how to use light and angles to take better photos.</p></div>
          </div>
          <div className="guest-example-footer"><span><Coins size={18} /> 1 credit per session</span><button className="primary" onClick={requireSignup}>Find a mentor <ArrowRight size={16} /></button></div>
        </aside>
      </section>
      <section className="guest-how-it-works" id="guest-how-it-works" aria-labelledby="guest-how-title"><h2 id="guest-how-title">How GotTime works</h2><div className="guest-lanes">{lanes.map(({ icon: Icon, title, text }, index) => <article className="guest-step" key={title}><div className="guest-step-top"><Icon size={23} /><small>Step {index + 1}</small></div><h3>{title}</h3><p>{text}</p></article>)}</div></section>
      <section className="guest-section"><div className="guest-section-head"><h2>Explore mentors</h2><p>Preview the kind of sessions learners can book after signing up.</p></div><div className="guest-mentor-grid">{mentorCards.map((card) => <article className={`guest-mentor`} key={card.title}><span>{card.icon}</span><small>{card.tag}</small><h3>{card.title}</h3><p>{card.text}</p><button onClick={requireSignup}>View details <LockKeyhole size={14} /></button></article>)}</div></section>
      <SiteFooter onAuth={onAuth} />
      {showPrompt && <div className="guest-pop"><button onClick={() => setShowPrompt(false)}><X size={15} /></button><span><Coins size={18} /></span><div><b>Get 10 starter credits</b><p>Sign up to book mentors.</p></div><button className="primary" onClick={requireSignup}>Join now</button></div>}
    </main>
  );
}

function EntryChoice({ onChoose }) {
  return <main className="entry-choice-page"><section className="entry-choice-card"><Logo tagline /><span className="eyebrow"><ShieldCheck size={15} /> Choose your GotTime door</span><h1>Are you entering as admin or user?</h1><p>This choice is remembered in this browser. Clear site data/history if you want the app to ask again.</p><div className="entry-choice-actions"><button className="entry-card admin-entry" onClick={() => onChoose("admin")}><span><ShieldCheck size={25} /></span><b>Admin</b><small>Manage users, reports, and app activity.</small><ArrowRight size={18} /></button><button className="entry-card user-entry" onClick={() => onChoose("user")}><span><Users size={25} /></span><b>User</b><small>Create your account and start trading time.</small><ArrowRight size={18} /></button></div></section></main>;
}

function Auth({ onAuth, initialMode = "login", onBack, adminOnly = false }) {
  const [mode, setMode] = useState(adminOnly ? "login" : initialMode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({ username: "", email: "", password: "" });
  const googleHref = "/auth/google";
  const startGoogle = () => clearAuthToken();
  useEffect(() => setMode(adminOnly ? "login" : initialMode), [initialMode, adminOnly]);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const authError = params.get("authError");
    const authDetail = params.get("authDetail");
    const savedNotice = sessionStorage.getItem("gt-auth-notice");
    if (savedNotice) {
      setNotice(savedNotice);
      sessionStorage.removeItem("gt-auth-notice");
    }
    if (!authError) return;
    const messages = { google_cancelled: "Google sign-in cancelled.", google_state: "Google sign-in expired before GotTime could finish. Try once more.", google_not_configured: "Google sign-in needs setup.", google_network: "Google sign-in could not reach Google.", google_token: "Google did not return an access token.", google_email: "Google did not share an email address.", google_server: "GotTime could not save this Google account." };
    setNotice(`${messages[authError] || "Google sign-in could not be completed."}${authDetail ? ` ${authDetail}` : ""}`);
    history.replaceState({}, "", location.pathname);
  }, []);
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try { const data = await api(adminOnly ? "/api/auth/admin-login" : `/api/auth/${mode}`, { method: "POST", body: JSON.stringify(form) }); storeAuthToken(data.authToken); onAuth(data.user); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  return (
    <main className="auth-page"><section className="auth-story"><div className="auth-nav"><Logo />{adminOnly && <span className="auth-trust"><ShieldCheck size={15} /> Admin area</span>}</div><div className="story-copy"><div className="eyebrow"><Sparkles size={16} /> {adminOnly ? "Control room" : "Trade hours. Learn skills."}</div><h1>{adminOnly ? <>Keep GotTime<br />running <em>clean.</em></> : <>Turn free hours<br />into <em>skills.</em></>}</h1><p>{adminOnly ? "Review users, reports, sessions, and platform activity from one calm place." : "Use credits to book mentors, learn for one hour, then earn credits back by teaching something you already know."}</p>{!adminOnly && <div className="story-points"><span><BookOpen size={17} /> Book one-hour sessions</span><span><Coins size={17} /> Spend credits, not cash</span><span><Sparkles size={17} /> Teach to earn more</span></div>}{adminOnly && <div className="story-proof"><div className="face-stack"><Avatar name="GT" /><Avatar name="Skill" index={1} /><Avatar name="Time" index={2} /></div><div><strong>Admin access only</strong><span>protected dashboard</span></div></div>}</div><div className="story-orbit orbit-one" /><div className="story-orbit orbit-two" /></section>
      <section className="auth-panel"><div className="auth-box"><div className="mobile-logo"><Logo /></div>{onBack && <button className="text-button back-home" onClick={onBack} type="button">Back to guest home</button>}{!adminOnly && <div className="auth-tabs"><button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>Sign in</button><button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>Create account</button></div>}<h2>{adminOnly ? "Admin sign in" : mode === "login" ? "Welcome back" : "Join the time economy"}</h2><p className="muted">{adminOnly ? "Use an admin account to continue." : mode === "login" ? "Your next skill is waiting." : "Start with ten credits on us."}</p><form onSubmit={submit}>{!adminOnly && mode === "register" && <label>Your name<input required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>}<label>Email address<input required type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label><label>Password<input required type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>{notice && <div className="form-notice">{notice}</div>}{error && <div className="form-error">{error}</div>}<button className="primary full" disabled={busy}>{busy ? "Opening..." : adminOnly ? "Enter admin" : mode === "login" ? "Sign in" : "Start my adventure"} <ArrowRight size={17} /></button></form>{!adminOnly && <><div className="or"><span>or</span></div><a className="google-button" href={googleHref} onClick={startGoogle}><b>G</b> Continue with Google</a></>}</div></section></main>
  );
}

function BirthdateGate({ onComplete, onLogout }) {
  const currentYear = new Date().getFullYear();
  const [birthdateParts, setBirthdateParts] = useState({ day: "", month: "", year: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const birthdate = birthdateParts.year && birthdateParts.month && birthdateParts.day
    ? `${birthdateParts.year}-${birthdateParts.month.padStart(2, "0")}-${birthdateParts.day.padStart(2, "0")}`
    : "";
  const setPart = (key, value) => setBirthdateParts((current) => ({ ...current, [key]: value }));
  const submit = async (event) => {
    event.preventDefault();
    if (!birthdate) {
      setError("Choose your day, month, and year.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api("/api/auth/birthdate", { method: "POST", body: JSON.stringify({ birthdate }) });
      storeAuthToken(result.authToken);
      onComplete(result.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return <main className="auth-page birthdate-page"><section className="auth-story"><div className="auth-nav"><Logo /><span className="auth-trust"><ShieldCheck size={15} /> Safety check</span></div><div className="story-copy"><div className="eyebrow"><CalendarDays size={16} /> Age verification</div><h1>One quick<br />security <em>check.</em></h1><p>We need your birthdate to keep GotTime safe and unlock age-aware features.</p></div><div className="story-orbit orbit-one" /><div className="story-orbit orbit-two" /></section><section className="auth-panel"><form className="auth-box birthdate-box" onSubmit={submit}><div className="mobile-logo"><Logo /></div><h2>Enter your birthdate</h2><p className="muted">This is mandatory unless your Google account already shared a verified birthdate.</p><label>Birthdate<div className="birthdate-picker"><select required value={birthdateParts.day} onChange={(e) => setPart("day", e.target.value)}><option value="">Day</option>{Array.from({ length: 31 }, (_, i) => <option key={i + 1} value={String(i + 1)}>{i + 1}</option>)}</select><select required value={birthdateParts.month} onChange={(e) => setPart("month", e.target.value)}><option value="">Month</option>{months.map((month, i) => <option key={month} value={String(i + 1)}>{month}</option>)}</select><select required value={birthdateParts.year} onChange={(e) => setPart("year", e.target.value)}><option value="">Year</option>{Array.from({ length: 100 }, (_, i) => currentYear - i).map((year) => <option key={year} value={String(year)}>{year}</option>)}</select></div></label>{error && <div className="form-error">{error}</div>}<button className="primary full" disabled={busy}>{busy ? "Checking..." : "Verify and enter"} <ArrowRight size={17} /></button><button className="text-button full" type="button" onClick={onLogout}>Sign out</button></form></section></main>;
}

function Sidebar({ page, setPage, user, data, onLogout, collapsed, setCollapsed }) {
  const unread = Number(data?.unreadMessages || 0);
  const xp = Number(user.xp || 0), nextMilestone = xp < 500 ? 500 : Math.ceil((xp + 1) / 500) * 500;
  const visibleNav = navItems.filter((item) => !item.adminOnly || user.role === "admin");
  return <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>{!collapsed && <button className="sidebar-edge-toggle" type="button" onClick={() => setCollapsed(true)} aria-label="Collapse menu"><ChevronLeft size={18} /></button>}<Logo compact={collapsed} tagline onClick={() => setPage("home")} /><nav>{visibleNav.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? "active" : ""} onClick={() => { if (collapsed) setCollapsed(false); setPage(id); }}><Icon size={19} /><span>{label}</span>{id === "messages" && unread > 0 && <i>{unread}</i>}</button>)}</nav><div className="level-card"><span className="level-badge"><Zap size={16} /> {xp}</span><div><b>{xp >= 500 ? "Legend" : xp >= 250 ? "Seeker" : "Explorer"}</b><small>{xp} / {nextMilestone} XP</small></div><div className="progress"><span style={{ width: `${Math.min(100, (xp / nextMilestone) * 100)}%` }} /></div></div><button className="side-profile" onClick={() => { if (collapsed) setCollapsed(false); setPage("profile"); }}><Avatar name={user.username} profilePhoto={user.profile_photo} size="sm" /><span><b>{user.username}</b><small>{user.role === "admin" ? "Admin" : memberLabel()}</small></span></button><button className="side-logout" onClick={onLogout}><LogOut size={17} /><span>Sign out</span></button></aside>;
}

function Header({ page, user, data, theme, setTheme, setPage, onReadNotifications, onNotificationClick, onLogout }) {
  const titles = { home: "Home base", discover: "Discover mentors", "mentor-detail": "Mentor details", sessions: "My sessions", calendar: "Calendar", skills: "My skill deck", badges: "Badge vault", messages: "Messages", notifications: "Notifications", leaderboard: "Leaderboard", profile: "Your profile", subscription: "Buy credits", donation: "Support GotTime", testimonials: "Testimonials", about: "About GotTime", admin: "Admin" };
  const unread = data.notifications.filter((item) => !item.is_read).length;
  const [open, setOpen] = useState(false);
  const notificationRef = useRef(null);
  const notifications = data.notifications.slice(0, 6);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event) => {
      if (notificationRef.current && !notificationRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);
  const toggleNotifications = async () => { const next = !open; setOpen(next); if (next && unread > 0) await onReadNotifications(); };
  return <header className="topbar"><div className="topbar-brand"><h2>{titles[page]}</h2></div><div className="top-actions"><button className="icon-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")}>{theme === "light" ? <Moon size={19} /> : <Sun size={19} />}</button><button className="top-donate-button" type="button" onClick={() => setPage("donation")}><Sparkles size={16} /> Donate</button><button className="top-buy-button" type="button" onClick={() => setPage("subscription")}><Coins size={16} /> Buy credits</button><div className="notification-wrap" ref={notificationRef}><button className="icon-button notification-button" onClick={toggleNotifications} aria-label="Notifications"><Bell size={19} />{unread > 0 && <i>{unread}</i>}</button>{open && <section className="notification-menu"><div className="notification-head"><b>Notifications</b><button onClick={() => setOpen(false)}>Close</button></div>{notifications.length ? notifications.map((item) => <button className={`notification-item ${String(item.message || "").toLowerCase().includes("missed") ? "missed-notification" : ""}`} key={item.id} onClick={() => { setOpen(false); onNotificationClick(item); }}><span className={`dot ${item.is_read ? "" : "new"}`} /><p>{item.type === "message" && item.sender_name ? <><b>{item.sender_name}</b> sent you a message{item.message_preview ? <small>{item.message_preview}</small> : null}</> : item.message}</p></button>) : <div className="notification-empty">No notifications</div>}</section>}</div><div className="profile-menu"><button className="compact-profile" onClick={() => setPage("profile")}><Avatar name={user.username} profilePhoto={user.profile_photo} size="xs" /><span>{user.username}</span></button><div className="profile-hover-card"><b>{user.username}</b><small>{user.credits || 0} credits</small><button type="button" onClick={() => setPage("profile")}>Profile overview</button><button type="button" onClick={() => setPage("subscription")}>Credits & packs</button><button type="button" onClick={() => setPage("profile")}>Account settings</button><button type="button" className="danger" onClick={onLogout}>Sign out</button></div></div></div></header>;
}

function StatCard({ icon: Icon, label, value, note, tone = "mint" }) { return <article className={`stat-card tone-${tone}`}><div className="stat-icon"><Icon size={21} /></div><div><span>{label}</span><strong>{value}</strong><small>{note}</small></div></article>; }
function EmptyState({ icon: Icon, title, text, action, onAction }) { return <div className="empty-state"><span><Icon size={25} /></span><div><b>{title}</b><p>{text}</p></div>{action && <button className="mini-button" onClick={onAction}>{action}</button>}</div>; }
function AdminPage({ data, notify }) {
  const [adminData, setAdminData] = useState(null);
  const [busyUser, setBusyUser] = useState(null);
  const loadAdmin = async () => {
    try { setAdminData(await api("/api/admin")); }
    catch (e) { notify(e.message || "Admin dashboard could not load.", true); }
  };
  useEffect(() => { loadAdmin(); }, []);
  const deactivate = async (userId) => {
    setBusyUser(userId);
    try {
      const result = await api(`/api/admin/remove-user/${userId}`, { method: "POST" });
      notify(result.message || "Account updated.");
      await loadAdmin();
    } catch (e) {
      notify(e.message || "Could not update that account.", true);
    } finally {
      setBusyUser(null);
    }
  };
  if (data.user.role !== "admin") return <div className="page"><EmptyState icon={ShieldCheck} title="Admin access only" text="This page is reserved for GotTime admins." /></div>;
  const stats = adminData?.stats || {};
  return <div className="page admin-page"><section className="page-title-row"><div><span className="eyebrow"><ShieldCheck size={15} /> GotTime control room</span><h1>Admin <em>dashboard.</em></h1><p>Manage people, reports, and platform activity from one clean place.</p></div><button className="soft-button" onClick={loadAdmin}><Sparkles size={16} /> Refresh</button></section><section className="stats-grid admin-stats"><StatCard icon={Users} label="Users" value={stats.users ?? "-"} note="active accounts" /><StatCard icon={CalendarDays} label="Sessions" value={stats.sessions ?? "-"} note="total requests" tone="blue" /><StatCard icon={Bell} label="Notifications" value={stats.notifications ?? "-"} note="sent in app" tone="violet" /><StatCard icon={ShieldCheck} label="Reports" value={stats.reports ?? "-"} note="needs review" tone="peach" /></section><section className="content-card admin-card"><div className="card-head"><div><h3>Accounts</h3><p>Deactivate problem accounts without touching admin users.</p></div></div>{adminData ? <div className="admin-table">{adminData.users.map((u) => <div className="admin-row" key={u.id}><Avatar name={u.username} profilePhoto={u.profile_photo} /><span><b>{u.username}</b><small>{u.email}</small></span><strong>{u.credits || 0}h</strong><small>{u.sessions_completed || 0} sessions</small>{u.role === "admin" ? <i>Admin</i> : <button className="mini-button danger" disabled={busyUser === u.id || u.id === data.user.id} onClick={() => deactivate(u.id)}>{busyUser === u.id ? "Updating..." : "Deactivate"}</button>}</div>)}</div> : <EmptyState icon={Users} title="Loading accounts" text="Gathering the latest app data." />}</section><section className="content-card admin-card"><div className="card-head"><div><h3>Reports</h3><p>User reports that may need attention.</p></div></div>{adminData?.reports?.length ? <div className="admin-report-list">{adminData.reports.map((report) => <article key={report.id}><span><b>{report.reporter_name || "Someone"}</b> reported <b>{report.reported_name || "a user"}</b></span><p>{report.reason || "No reason provided."}</p><small>{formatDate(report.created_at)}</small></article>)}</div> : <EmptyState icon={ShieldCheck} title="No reports" text="Everything looks calm right now." />}</section></div>;
}
function DashboardTestimonials({ setPage }) {
  const stories = [
    { name: "Ananya", quote: "One design critique fixed my portfolio.", tag: "Design" },
    { name: "Rohit", quote: "Teaching Python paid for music lessons.", tag: "Programming" },
    { name: "Meera", quote: "It feels like people helping people.", tag: "Skill swap" },
  ];
  return <section className="content-card dashboard-stories"><div className="card-head"><div><h3>Skill wins</h3><p>Small stories from real skill swaps</p></div><button className="text-button" onClick={() => setPage("testimonials")}>View all <ArrowRight size={15} /></button></div><div className="dashboard-story-grid">{stories.map((story, i) => <button type="button" className={`dashboard-story card-${gradients[i % gradients.length]}`} key={story.name} onClick={() => setPage("testimonials")}><div><Avatar name={story.name} index={i} /><span><b>{story.name}</b><small>{story.tag}</small></span></div><p>"{story.quote}"</p><small>{memberLabel()}</small></button>)}</div></section>;
}
function SessionRow({ item, user, action, onOpen, openQuiz, openChat }) {
  const isMentor = item.mentor_id === user.id;
  const chatContactId = sessionChatContact(item, user.id);
  const other = isMentor ? item.learner_name : item.mentor_name;
  const available = [];
  const quizReadyForLearner = !isMentor && item.has_quiz && ["awaiting_confirmation", "completed"].includes(item.status);
  const mentorCanEditQuiz = isMentor && ["pending", "accepted", "scheduled"].includes(item.status);
  const joined = isMentor ? item.mentor_joined_at : item.learner_joined_at;
  if (item.mode === "offline" && !joined && ["accepted", "scheduled"].includes(item.status)) available.push(["join", "Start session"]);
  if (isMentor && item.status === "pending") available.push(["accept", "Accept"], ["decline", "Decline"]);
  if (isMentor && item.status === "accepted" && !item.scheduled_for) available.push(["schedule", "Schedule"]);
  if (isMentor && ["scheduled", "accepted"].includes(item.status)) available.push(["complete", "Mark done"]);
  if (!isMentor && item.status === "awaiting_confirmation") available.push(["confirm", "Confirm"]);
  return <article className="session-row" onClick={onOpen}>
    <div className={`session-date ${item.mode === "offline" ? "peach" : ""}`}><b>{formatDate(item.scheduled_for, { day: "2-digit" }).split(" ")[1] || "-"}</b><span>{formatDate(item.scheduled_for).split(" ")[0]}</span></div>
    <div className="session-main">
      <b>{item.skill_name || "Skill session"}</b>
      <span>with {other} - {item.mode}</span>
      <small><Clock3 size={14} /> {formatSessionTime(item.scheduled_for)}</small>
      {item.has_quiz && <small><Clipboard size={14} /> Quiz: {item.quiz_submitted ? `${item.quiz_score}/${item.quiz_total}` : `${item.quiz_question_count} MCQs`}</small>}
    </div>
    <span className={`status status-${item.status}`}>{String(item.status).replace("_", " ")}</span>
    <div className="row-actions">
      {openChat && chatContactId && <button type="button" className="mini-button" onClick={(e) => { e.stopPropagation(); openChat(chatContactId); }}><MessageCircle size={14} /> Chat</button>}
      {mentorCanEditQuiz && <button type="button" className="mini-button" onClick={(e) => { e.stopPropagation(); openQuiz?.(item, "build"); }}><Clipboard size={14} /> {item.has_quiz ? "Edit quiz" : "Create quiz"}</button>}
      {quizReadyForLearner && <button type="button" className="mini-button" onClick={(e) => { e.stopPropagation(); openQuiz?.(item, "take"); }}>{item.quiz_submitted ? "View score" : "Take quiz"}</button>}
      {available.length ? available.map(([k, l]) => <button key={k} type="button" className={k === "decline" ? "text-button" : "mini-button"} onClick={(e) => { e.stopPropagation(); action(item.id, k); }}>{l}</button>) : <button type="button" className="icon-button row-open" onClick={(e) => { e.stopPropagation(); onOpen?.(); }} aria-label="Open scheduled session"><ChevronRight size={18} /></button>}
    </div>
  </article>;
}
function Dashboard({ data, setPage, openBooking, sessionAction, openChat, viewedMentors = [] }) {
  const { user, sessions, notifications, transactions, badges } = data;
  const upcoming = sessions.filter((s) => !["completed", "cancelled", "missed"].includes(s.status)).slice(0, 3);
  const learned = sessions.filter((s) => s.status === "completed" && s.learner_id === user.id).length;
  const taught = sessions.filter((s) => s.status === "completed" && s.mentor_id === user.id).length;
  const totalXp = Number(user.xp || 0), nextXpMilestone = totalXp < 50 ? 50 : totalXp < 250 ? 250 : totalXp < 500 ? 500 : Math.ceil((totalXp + 1) / 500) * 500;
  const xp = Math.min(100, (totalXp / nextXpMilestone) * 100);
  const feed = (notifications.filter((n) => n.type !== "message" || !n.is_read).length ? notifications.filter((n) => n.type !== "message" || !n.is_read) : transactions).slice(0, 3);
  const quest = data.dailyQuest || fallbackDailyQuest();
  const questTarget = Number(quest.target || 1);
  const questCount = dailyQuestCount(data, viewedMentors);
  const questProgress = `${(questCount / questTarget) * 100}%`;
  return <div className="page dashboard-page"><section className="welcome-banner"><div><h1>Hey {user.username.split(" ")[0]}, what will you<br /><em>unlock today?</em></h1><p>Every hour shared makes the whole exchange richer.</p><div className="banner-actions"><button className="primary" onClick={() => setPage("discover")}>Find a mentor <ArrowRight size={17} /></button><button className="soft-button" onClick={() => setPage("skills")}><Plus size={17} /> Share a skill</button></div></div><div className="quest-ring" style={{ "--quest-progress": `${xp}%` }}><div><Sparkles size={25} /><strong>{totalXp}</strong><span>total XP</span></div></div></section><section className="stats-grid"><StatCard icon={Clock3} label="Time credits" value={`${user.credits}h`} note="Available balance" /><StatCard icon={Zap} label="XP earned" value={totalXp} note="Includes daily-quest bonus XP" tone="mint" /><StatCard icon={BookOpen} label="Skills learned" value={learned} note="Keep exploring" tone="violet" /><StatCard icon={Sparkles} label="Hours shared" value={`${taught}h`} note="Your teaching impact" tone="blue" /></section><div className="dashboard-columns"><section className="content-card sessions-card"><div className="card-head"><div><h3>Up next</h3><p>Your learning calendar</p></div><button className="text-button" onClick={() => setPage("sessions")}>View all <ArrowRight size={15} /></button></div><div className="session-list">{upcoming.length ? upcoming.map((item) => <SessionRow key={item.id} item={item} user={user} action={sessionAction} openChat={openChat} onOpen={() => setPage("calendar")} />) : <EmptyState icon={CalendarDays} title="Your calendar is wide open" text="Find someone brilliant and book your first skill swap." action="Browse mentors" onAction={openBooking} />}</div></section><section className={`content-card daily-quest ${data.dailyQuestClaimed ? "completed" : ""}`}><div className="card-head"><div><h3>Daily quest</h3><p>Resets every 24 hours</p></div><span className={`xp-chip ${data.dailyQuestClaimed ? "claimed" : ""}`}>{data.dailyQuestClaimed ? "Completed" : `+${quest.xp || 50} XP`}</span></div><div className="quest-art"><Target size={34} /></div><h4>{quest.title}</h4><p>{quest.description}</p><div className="progress"><span style={{ width: questProgress }} /></div><small>{`${questCount} of ${questTarget} done`}</small></section></div><div className="dashboard-columns lower"><section className="content-card activity-card"><div className="card-head"><div><h3>Your momentum</h3><p>Hours invested over 6 weeks</p></div></div><div className="mini-chart empty-chart">{[0, 0, 0, 0, 0, 0].map((h, i) => <div key={i}><span style={{ height: `${h}%` }} /><small>W{i + 1}</small></div>)}</div></section><section className="content-card feed-card"><div className="card-head"><div><h3>Updates</h3><p>Things worth checking</p></div><button className="text-button" onClick={() => setPage("notifications")}>View all <ArrowRight size={15} /></button></div>{feed.length ? feed.map((item, i) => <button className={`feed-item ${String(item.message || item.note || "").toLowerCase().includes("missed") ? "missed-notification" : ""}`} key={item.id || i} onClick={() => setPage("notifications")}><span className={`feed-icon ${gradients[i]}`}>{item.type === "badge" ? <Award size={17} /> : item.type === "xp" ? <Zap size={17} /> : item.kind === "earned" ? <Coins size={17} /> : <Bell size={17} />}</span><div><b>{item.type === "message" && item.sender_name ? `${item.sender_name} sent you a message` : item.message || item.note}</b><small>{formatDate(item.created_at)}</small></div></button>) : <EmptyState icon={Bell} title="No updates yet" text="New session, badge, and credit updates will appear here." />}</section></div><DashboardTestimonials setPage={setPage} /></div>;
}

function Discover({ data, openMentor }) {
  const [query, setQuery] = useState(""), [category, setCategory] = useState("All"), [viewMode, setViewMode] = useState("grid");
  const categories = ["All", ...new Set(data.skills.map((s) => s.category).filter(Boolean))].slice(0, 6);
  const mentors = data.mentors.filter((m) => `${m.username} ${m.skills} ${m.categories} ${m.bio} ${m.primary_skill} ${m.primary_description}`.toLowerCase().includes(query.toLowerCase()) && (category === "All" || (m.categories || "").toLowerCase().split(",").map((c) => c.trim()).includes(category.toLowerCase())));
  const canBookMentors = Number(data.user?.credits || 0) >= 1;
  return <div className="page"><section className="page-intro"><div><span className="eyebrow"><Compass size={15} /> Learn from real people</span><h1>Find your next <em>spark.</em></h1><p>Browse mentors who trade expertise for time-not money.</p></div><div className="discover-tools"><div className="search-box"><Search size={19} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search skills or mentors..." /></div><div className="view-toggle" aria-label="Mentor view mode"><button type="button" className={viewMode === "grid" ? "active" : ""} onClick={() => setViewMode("grid")} aria-label="Grid view"><LayoutGrid size={17} /></button><button type="button" className={viewMode === "list" ? "active" : ""} onClick={() => setViewMode("list")} aria-label="List view"><List size={17} /></button></div></div></section><div className="filter-row">{categories.map((c) => <button key={c} className={category === c ? "active" : ""} onClick={() => setCategory(c)}>{c}</button>)}</div><section className={`mentor-detail-grid ${viewMode === "list" ? "mentor-list-view" : ""}`}>{mentors.length ? mentors.map((mentor, i) => { const skillList = mentorSkills(mentor); const primarySkill = mentorPrimarySkill(mentor); return <button type="button" className={`mentor-detail-card mentor-preview-card ${canBookMentors ? "" : "disabled"}`} key={mentor.mentor_skill_id || `${mentor.id}-${primarySkill}`} disabled={!canBookMentors} onClick={() => canBookMentors && openMentor(mentor)}><div className={`mentor-cover cover-${mentor.banner_style && gradients.includes(mentor.banner_style) ? mentor.banner_style : gradients[i % gradients.length]} ${mentor.banner_image ? "custom-banner-cover" : ""}`} style={mentor.banner_image ? { backgroundImage: `linear-gradient(rgba(255,253,248,.08), rgba(255,253,248,.08)), url("${bannerImageUrl(mentor.banner_image)}")` } : {}}><span><MentorCoverIcon mentor={mentor} /></span></div><div className="mentor-detail-body"><div className="mentor-name"><Avatar name={mentor.username} profilePhoto={mentor.profile_photo} index={i} /><div><h3>{mentor.username}</h3><span><Star size={14} fill="currentColor" /> {formatRating(mentor.rating, "New")} - {mentor.sessions_completed || 0} sessions</span></div></div><h4>{primarySkill}</h4><p>{mentorPrimaryDescription(mentor)}</p><div className="tag-row">{skillList.slice(0, 5).map((skill) => <span key={`${mentor.id}-${skill}`}>{skill}</span>)}</div><div className="mentor-detail-meta"><span><Clock3 size={15} /> {mentor.duration || 60} min sessions</span><span><Video size={15} /> Preferred: {modeLabel(mentor.availability)}</span><span><Coins size={15} /> 1 credit</span><span><Clock3 size={15} /> {preferredTimeLabel(mentor.preferred_time)}</span></div><span className="mentor-view-more">{canBookMentors ? "View details" : "Need 1 credit"} <ArrowRight size={15} /></span></div></button>; }) : <EmptyState icon={Search} title="No mentors yet" text="Add a skill from My skills to make that account appear here." />}</section></div>;
}
function MentorDetailPage({ data, mentor, setPage, openBooking, openChat, onMentorViewed, saveBanner }) {
  const [bannerOpen, setBannerOpen] = useState(false), [bannerBusy, setBannerBusy] = useState(false);
  mentor = refreshProfileStats(mentor, data);
  if (!mentor) return <div className="page"><EmptyState icon={Compass} title="Choose a mentor" text="Open a mentor from Discover to see their details." action="Back to Discover" onAction={() => setPage("discover")} /></div>;
  const skills = mentorSkills(mentor);
  const primarySkill = mentorPrimarySkill(mentor);
  const isOwnMentor = data?.user?.id === mentor.id;
  const bannerStyle = mentor.banner_style && gradients.includes(mentor.banner_style) ? mentor.banner_style : gradients[Number(mentor.id || 0) % gradients.length];
  const bannerInline = mentor.banner_image ? { backgroundImage: `linear-gradient(rgba(255,253,248,.08), rgba(255,253,248,.08)), url("${bannerImageUrl(mentor.banner_image)}")` } : {};
  const chooseDefault = async (style) => { setBannerBusy(true); try { await saveBanner({ style }); setBannerOpen(false); } finally { setBannerBusy(false); } };
  const chooseFile = async (e) => { const file = e.target.files?.[0]; if (!file) return; setBannerBusy(true); try { await saveBanner({ file }); setBannerOpen(false); } finally { setBannerBusy(false); e.target.value = ""; } };
  return <div className="page mentor-profile-page"><button className="text-button back-link" onClick={() => setPage("discover")}>Back to mentors</button><article className="mentor-detail-card mentor-full-card"><div className={`mentor-cover cover-${bannerStyle} ${mentor.banner_image ? "custom-banner-cover" : ""}`} style={bannerInline}><span><MentorCoverIcon mentor={mentor} /></span>{isOwnMentor && <button type="button" className="banner-edit-button" aria-label="Edit banner" onClick={() => setBannerOpen(true)}><Pencil size={15} /></button>}</div><div className="mentor-detail-body"><div className="mentor-name"><Avatar name={mentor.username} profilePhoto={mentor.profile_photo} size="lg" /><div><h1>{mentor.username}</h1><span><Star size={14} fill="currentColor" /> {formatRating(mentor.rating, "New")} - {mentor.sessions_completed || 0} sessions</span></div></div><h2 className="mentor-skill-title">{primarySkill}</h2><p>{mentorPrimaryDescription(mentor)}</p><div className="tag-row">{skills.map((skill) => <span key={`${mentor.id}-${skill}`}>{skill}</span>)}</div><div className="mentor-detail-meta"><span><Clock3 size={15} /> {mentor.duration || 60} min sessions</span><span><Video size={15} /> Preferred: {modeLabel(mentor.availability)}</span><span><Coins size={15} /> 1 credit</span><span><Clock3 size={15} /> {preferredTimeLabel(mentor.preferred_time)}</span></div><div className="mentor-detail-actions"><button className="primary" onClick={() => { onMentorViewed?.(mentor.id); openChat(mentor.id); }}><MessageCircle size={17} /> Chat with mentor</button><button className="soft-button" onClick={() => { onMentorViewed?.(mentor.id); openBooking(mentor); }}>Book session <ArrowRight size={17} /></button></div><p className="mentor-booking-note">You can chat first or book directly. Either way, once a date and time is set, it appears in both calendars and My sessions.</p></div></article>{bannerOpen && <Modal title="Edit banner" subtitle="Pick a default look or upload your own banner." onClose={() => setBannerOpen(false)}><div className="banner-picker"><div className="banner-default-grid">{bannerOptions.map((option) => <button type="button" key={option.id} className={`banner-choice cover-${option.id}`} disabled={bannerBusy} onClick={() => chooseDefault(option.id)}><span>{option.label}</span>{bannerStyle === option.id && !mentor.banner_image && <Check size={16} />}</button>)}</div><div className="banner-upload-row"><label className="soft-button">Gallery / files<input type="file" accept="image/*" disabled={bannerBusy} onChange={chooseFile} /></label><label className="soft-button">Camera<input type="file" accept="image/*" capture="environment" disabled={bannerBusy} onChange={chooseFile} /></label></div></div></Modal>}</div>;
}
function SessionsPage({ data, action, openBooking, openQuiz, openChat, setPage }) {
  const [filter, setFilter] = useState("upcoming");
  const shown = data.sessions.filter((s) => filter === "all" || (filter === "past" ? ["completed", "cancelled", "missed"].includes(s.status) : !["completed", "cancelled", "missed"].includes(s.status)));
  return <div className="page"><section className="page-title-row"><div><span className="eyebrow"><CalendarDays size={15} /> Your time, well spent</span><h1>Session <em>quests.</em></h1><p>Manage every skill swap from request to reward.</p></div><button className="primary" onClick={openBooking}><Plus size={17} /> Book a session</button></section><div className="segmented">{["upcoming", "past", "all"].map((f) => <button className={filter === f ? "active" : ""} key={f} onClick={() => setFilter(f)}>{f}</button>)}</div><section className="content-card session-page-card">{shown.length ? shown.map((item) => <SessionRow key={item.id} item={item} user={data.user} action={action} openQuiz={openQuiz} openChat={openChat} onOpen={() => setPage("calendar")} />) : <EmptyState icon={CalendarDays} title="No sessions here" text="A blank calendar is just an adventure waiting to happen." action="Find a mentor" onAction={openBooking} />}</section></div>;
}
function CalendarPage({ data, setPage }) {
  const [windowStart, setWindowStart] = useState(() => {
    const date = new Date();
    return new Date(date.getFullYear(), date.getMonth(), 1);
  });
  const scheduled = [...data.sessions].filter((s) => s.scheduled_for && !["cancelled"].includes(s.status)).sort((a, b) => parseScheduledDate(a.scheduled_for) - parseScheduledDate(b.scheduled_for));
  const people = [data.user, ...data.contacts, ...data.mentors].filter(Boolean);
  const birthdayPeople = people.filter((p) => p.birthday || p.birthdate || p.dob);
  const goMonth = (offset) => setWindowStart((current) => new Date(current.getFullYear(), current.getMonth() + offset, 1));
  const windowEnd = new Date(windowStart.getFullYear(), windowStart.getMonth() + 1, 0);
  const daysInMonth = windowEnd.getDate();
  const windowTitle = windowStart.toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "Asia/Kolkata" });
  const firstDayOffset = (windowStart.getDay() + 6) % 7;
  const monthDays = Array.from({ length: firstDayOffset + daysInMonth }, (_, i) => {
    if (i < firstDayOffset) return { empty: true, key: `blank-${i}`, sessions: [], birthdays: [] };
    const date = new Date(windowStart.getFullYear(), windowStart.getMonth(), i - firstDayOffset + 1);
    const key = date.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    const sessions = scheduled.filter((s) => parseScheduledDate(s.scheduled_for)?.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }) === key);
    const birthdays = birthdayPeople.filter((p) => {
      const raw = p.birthday || p.birthdate || p.dob;
      const bd = new Date(raw);
      return !Number.isNaN(bd.valueOf()) && bd.getMonth() === date.getMonth() && bd.getDate() === date.getDate();
    });
    return { date, sessions, birthdays, key };
  });
  const nextItems = scheduled.filter((item) => {
    const when = parseScheduledDate(item.scheduled_for);
    return when && when >= windowStart && when <= new Date(windowEnd.getFullYear(), windowEnd.getMonth(), windowEnd.getDate(), 23, 59, 59);
  }).slice(0, 5);
  return <div className="page calendar-page"><section className="calendar-hero"><div><span className="eyebrow"><CalendarDays size={15} /> Your time map</span><h1>Calendar <em>board.</em></h1><p>Birthdays, booked sessions, and the learning moments this month.</p></div><button className="primary" onClick={() => setPage("sessions")}><ArrowRight size={17} /> Manage sessions</button></section><section className="calendar-layout"><div className="calendar-card month-card"><div className="card-head calendar-window-head"><div><h3>{windowTitle}</h3><p>Tap a session to manage it</p></div><div className="calendar-window-controls"><button type="button" onClick={() => goMonth(-1)} aria-label="Previous month"><ArrowLeft size={17} /></button><button type="button" onClick={() => goMonth(1)} aria-label="Next month"><ArrowRight size={17} /></button></div></div><div className="calendar-weekdays">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => <span key={day}>{day}</span>)}</div><div className="calendar-grid">{monthDays.map(({ date, sessions, birthdays, empty, key }) => empty ? <article className="calendar-day empty" key={key} /> : <article className={`calendar-day ${sessions.length || birthdays.length ? "has-event" : ""}`} key={date.toISOString()}><b>{date.toLocaleDateString("en-IN", { day: "2-digit", timeZone: "Asia/Kolkata" })}</b><div>{sessions.slice(0, 2).map((s) => <button key={s.id} className={s.status === "missed" ? "missed-calendar-event" : ""} onClick={() => setPage("sessions")}>{s.status === "completed" ? <Check size={12} role="img" aria-label="Completed session" /> : <Clock3 size={12} />} <span><b>{s.skill_name || "Session"}</b><small>{s.status === "missed" ? "Missed" : formatTimeIST(s.scheduled_for)}</small></span></button>)}{birthdays.slice(0, 1).map((p) => <small key={`b-${p.id}`}>?? {p.username}</small>)}</div></article>)}</div></div><aside className="calendar-card agenda-card"><div className="card-head"><div><h3>This month</h3><p>Sessions with timings</p></div></div>{nextItems.length ? nextItems.map((item) => { const isMentor = item.mentor_id === data.user.id, other = isMentor ? item.learner_name : item.mentor_name; return <button className={`agenda-item ${item.status === "missed" ? "missed-agenda-item" : ""}`} key={item.id} onClick={() => setPage("sessions")}><span><b>{formatDate(item.scheduled_for, { day: "2-digit" }).split(" ")[1] || "-"}</b><small>{formatDate(item.scheduled_for).split(" ")[0]}</small></span><div><b>{item.skill_name || "Skill session"}</b><small>{item.status === "missed" ? `Missed - ${formatSessionTime(item.scheduled_for)} - with ${other}` : `${formatSessionTime(item.scheduled_for)} - with ${other}`}</small></div><ChevronRight size={17} /></button>; }) : <EmptyState icon={CalendarDays} title="Nothing this month" text="Move through the months or book a mentor to add a session." action="Find mentors" onAction={() => setPage("discover")} />}</aside><aside className="calendar-card birthday-card"><div className="card-head"><div><h3>Birthdays</h3><p>GotTime people</p></div></div>{birthdayPeople.length ? birthdayPeople.slice(0, 6).map((p, i) => <div className="birthday-item" key={p.id || i}><Avatar name={p.username} profilePhoto={p.profile_photo} index={i} /><span><b>{p.username}</b><small>{formatDate(p.birthday || p.birthdate || p.dob)}</small></span></div>) : <EmptyState icon={Sparkles} title="No birthdays added yet" text="Add birthdays to profiles to celebrate them here." />}</aside></section></div>;
}function uniqueSkillDeck(skills = []) {
  const seen = new Set();
  return skills.filter((skill) => {
    const key = String(skill.name || skill.skill_name || "").trim().toLowerCase();
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
function SkillsPage({ data, openSkill, editSkill, askRemoveSkill }) {
  const mySkills = uniqueSkillDeck(data.mySkills || []);
  return <div className="page"><section className="page-title-row"><div><span className="eyebrow"><BookOpen size={15} /> Your teaching inventory</span><h1>Your skill <em>deck.</em></h1><p>Every skill you share is another hour you can spend learning.</p></div><button className="primary" onClick={openSkill}><Plus size={17} /> Add a skill</button></section><section className="skill-summary"><div><Sparkles size={23} /><span><b>{mySkills.length} skills shared</b><small>Your knowledge is now discoverable</small></span></div><div className="skill-level"><span>Teaching level 0</span><div className="progress"><i style={{ width: "0%" }} /></div><b>0 XP</b></div></section><section className="skill-grid">{mySkills.map((skill, i) => <article className={`skill-card skill-${gradients[i % gradients.length]}`} key={skill.id || skill.skill_id || `${skill.name}-${i}`}><div className="skill-icon"><SkillCardIcon skill={skill} /></div><button className="icon-button skill-edit-button" onClick={() => editSkill(skill)} aria-label={`Edit ${skill.name || skill.skill_name || "skill"}`}><Pencil size={16} /></button><button className="icon-button" onClick={() => askRemoveSkill(skill)} aria-label={`Remove ${skill.name || skill.skill_name || "skill"}`}><X size={16} /></button><span className="skill-category">{skill.category}</span><h3>{skill.name || skill.skill_name || "Untitled skill"}</h3><p>{skill.description || "A practical, friendly session built around what the learner needs."}</p><div className="skill-meta"><span><Clock3 size={15} /> {skill.duration || 60} min</span><span><Video size={15} /> Preferred: {modeLabel(skill.availability)}</span><span><Clock3 size={15} /> {preferredTimeLabel(skill.preferred_time)}</span></div></article>)}<button className="add-skill-card" onClick={openSkill}><span><Plus size={24} /></span><b>Add your hidden talent</b><small>Someone out there wants to learn it</small></button></section></div>;
}

function MessagesPage({ data, refreshData, selectedContactId, sessionAction, showReward }) {
  const [pendingMeet, setPendingMeet] = useState(null);
  const [meetError, setMeetError] = useState("");
  const meetingWatchers = useRef(new Set());
  useEffect(() => () => meetingWatchers.current.forEach(clearInterval), []);
  // The server also includes accepted session partners who have not messaged yet.
  const realContacts = data.contacts;
  const findChatPerson = (id) => data.contacts.find((c) => c.id === Number(id)) || data.mentors.find((m) => m.id === Number(id)) || null;
  const [active, setActive] = useState(selectedContactId ? findChatPerson(selectedContactId) : realContacts[0] || null);
  const [messages, setMessages] = useState([]), [draft, setDraft] = useState(""), [menuId, setMenuId] = useState(null), [editing, setEditing] = useState(null), [toolsOpen, setToolsOpen] = useState(false), [meetOpen, setMeetOpen] = useState(false), [finishOpen, setFinishOpen] = useState(false), [finishChecked, setFinishChecked] = useState(false), [openedMeetSessions, setOpenedMeetSessions] = useState([]), [meetLink, setMeetLink] = useState(""), [editingMeetLink, setEditingMeetLink] = useState(false), [copiedMeetLink, setCopiedMeetLink] = useState(false), [scheduleOpen, setScheduleOpen] = useState(false), [scheduleDate, setScheduleDate] = useState(""), [scheduleTime, setScheduleTime] = useState(""), [scheduleBusy, setScheduleBusy] = useState(false), [scheduleTimeTouched, setScheduleTimeTouched] = useState(false), [scheduleError, setScheduleError] = useState(""), [deleteConfirm, setDeleteConfirm] = useState(null), [deletingMessage, setDeletingMessage] = useState(false), [ratingPrompt, setRatingPrompt] = useState(null), [ratingScore, setRatingScore] = useState(5), [ratingFeedback, setRatingFeedback] = useState(""), [ratingBusy, setRatingBusy] = useState(false);
  const consumedSelectedContact = useRef(null);
  const [messagePreviews, setMessagePreviews] = useState({});
  const currentContact = useRef(active?.id);
  currentContact.current = active?.id;
  const threadRequests = useRef(new Set());
  const mentorCloseSeen = useRef(new Map());
  const visibleContacts = (active && !realContacts.some((c) => c.id === active.id) ? [active, ...realContacts] : realContacts)
    .map((contact) => ({ ...contact, last_message: contact.last_message === "Encrypted message" ? messagePreviews[contact.id] || "Open conversation" : contact.last_message }));
  const activeSession = active ? [...data.sessions].filter((s) => ((s.learner_id === data.user.id && s.mentor_id === active.id) || (s.mentor_id === data.user.id && s.learner_id === active.id)) && !["completed", "cancelled", "missed"].includes(s.status)).sort((a, b) => (({ awaiting_confirmation: 0, scheduled: 1, accepted: 2, pending: 3 }[a.status] ?? 9) - ({ awaiting_confirmation: 0, scheduled: 1, accepted: 2, pending: 3 }[b.status] ?? 9)))[0] || null : null;
  const mentorClosedMeet = Boolean(activeSession?.mentor_left_at);
  const locallyOpenedMeet = activeSession && openedMeetSessions.includes(activeSession.id);
  const currentUserOpenedMeet = activeSession ? (data.user.id === activeSession.learner_id ? activeSession.learner_joined_at : activeSession.mentor_joined_at) || locallyOpenedMeet : false;
  const otherUserOpenedMeet = activeSession ? (data.user.id === activeSession.learner_id ? activeSession.mentor_joined_at : activeSession.learner_joined_at) : false;
  const bothOpenedMeet = Boolean(currentUserOpenedMeet && otherUserOpenedMeet);
  const canMentorCheck = activeSession && activeSession.mentor_id === data.user.id && ["accepted", "scheduled"].includes(activeSession.status);
  const canLearnerCheck = activeSession && activeSession.learner_id === data.user.id && activeSession.status === "awaiting_confirmation";
  const canMentorFinish = canMentorCheck && bothOpenedMeet;
  const canLearnerConfirm = canLearnerCheck && bothOpenedMeet;
  const canTickFinish = Boolean(mentorClosedMeet && currentUserOpenedMeet && (canMentorCheck || canLearnerCheck));
  const finishText = !activeSession ? "No booked session found for this chat yet. Book or request a session first, then credits and badges can be tracked." : !mentorClosedMeet ? "Waiting for the mentor to close the meeting. The finish check appears after that." : !currentUserOpenedMeet ? "Open the meeting link first to unlock your finish check." : !bothOpenedMeet ? "You can tick your side now. Rewards unlock only after the other person also opens the meeting link." : canMentorFinish ? "Tick the box when your side is done. The learner must tick and confirm from their side too." : canLearnerConfirm ? "Your mentor has already checked this session. Tick your box to complete it and unlock rewards for both sides." : activeSession.status === "pending" ? "This request is still pending. Finish tracking unlocks after the session is accepted." : "Waiting for the other side to finish their check.";
  const canScheduleSession = active && data.user.id !== active.id && (activeSession ? activeSession.mentor_id === data.user.id && !["completed", "cancelled"].includes(activeSession.status) : (data.mySkills || []).length > 0);
  const scheduleStorageKey = active ? `gt-chat-schedule-${data.user.id}-${active.id}` : "";
  const rememberedSchedule = scheduleStorageKey ? localStorage.getItem(scheduleStorageKey) || "" : "";
  const scheduleValueForDialog = activeSession?.scheduled_for || "";
  const toDateTimeLocalValue = (value) => {
    if (!value) return "";
    const raw = String(value).replace("T", " ").replace("Z", "");
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
    if (match) return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}`;
    const date = parseScheduledDate(value);
    if (!date) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  };
  const loadThread = async (id = active?.id) => {
    if (!id || threadRequests.current.has(id)) return;
    threadRequests.current.add(id);
    try {
      const r = await api(`/api/messages/${id}`);
      await ensureMessageKey(data.user.id);
      const migrated = await encryptLegacyThread(r.messages || [], data.user.id, id);
      const sourceMessages = migrated ? (await api(`/api/messages/${id}`)).messages || [] : r.messages || [];
      const decrypted = await Promise.all(sourceMessages.map(async (message) => ({ ...message, ...await decryptForMe(message, data.user.id) })));
      if (currentContact.current === id) setMessages(decrypted);
      const last = decrypted.at(-1);
      setMessagePreviews((previews) => ({ ...previews, [id]: last?.decryptionFailed ? "Message recovery pending" : last?.content || "On GotTime" }));
      await recoverReadableMessages(sourceMessages, data.user.id, id);
    }
    catch { /* Keep the current conversation visible during a temporary failure. */ }
    finally { threadRequests.current.delete(id); }
  };
  useEffect(() => { if (!selectedContactId || consumedSelectedContact.current === selectedContactId) return; const c = findChatPerson(selectedContactId); if (c) { setActive(c); consumedSelectedContact.current = selectedContactId; } }, [selectedContactId, data.contacts, data.mentors]);
  useEffect(() => { if (!active && !selectedContactId && realContacts.length) setActive(realContacts[0]); }, [active, selectedContactId, data.contacts]);
  useEffect(() => { loadThread(); setMenuId(null); setEditing(null); }, [active?.id]);
  useEffect(() => {
    if (!active?.id) return;
    const id = active.id;
    let cancelled = false;
    let timer;
    const poll = async () => {
      if (!document.hidden) await loadThread(id);
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 5000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [active?.id]);
  useEffect(() => {
    // App refresh is shared, non-overlapping, and pauses while the tab is hidden.
    return undefined;
  }, [activeSession?.id, activeSession?.mentor_id, activeSession?.status, data.user.id]);
  useEffect(() => {
    if (!activeSession?.id || ["completed", "cancelled"].includes(activeSession.status)) {
      setFinishOpen(false);
      return;
    }
    const closedAt = activeSession.mentor_left_at || "";
    const lastSeen = mentorCloseSeen.current.get(activeSession.id);
    if (lastSeen === undefined) {
      mentorCloseSeen.current.set(activeSession.id, closedAt);
      return;
    }
    mentorCloseSeen.current.set(activeSession.id, closedAt);
    if (!closedAt || closedAt === lastSeen) return;
    setFinishChecked(false);
    setFinishOpen(true);
  }, [activeSession?.id, activeSession?.status, activeSession?.mentor_left_at]);
  const send = async (e) => {
    e.preventDefault();
    if (!draft.trim() || !active) return;
    const content = draft.trim();
    try {
      const keyResult = await api(`/api/messages/key/${active.id}`);
      const encryptedContent = await buildEncryptedMessage({ senderId: data.user.id, receiverId: active.id, receiverPublicKey: keyResult.publicKey, content });
      if (!encryptedContent) throw new Error("Message encryption is not ready. Ask the other user to open GotTime once, then try again.");
      setMessages([...messages, { id: Date.now(), sender_id: data.user.id, content, created_at: new Date().toISOString() }]);
      setDraft(""); setToolsOpen(false);
      await api(`/api/messages/${active.id}`, { method: "POST", body: JSON.stringify({ content, encryptedContent }) });
      await loadThread(active.id);
    } catch (error) { alert(error.message || "Could not send encrypted message."); }
  };
  const saveEdit = async (message) => {
    const content = editing?.content?.trim();
    if (!content) return;
    const keyResult = await api(`/api/messages/key/${message.receiver_id === data.user.id ? message.sender_id : message.receiver_id}`);
    const receiverId = message.receiver_id === data.user.id ? message.sender_id : message.receiver_id;
    const encryptedContent = await buildEncryptedMessage({ senderId: data.user.id, receiverId, receiverPublicKey: keyResult.publicKey, content });
    await api(`/api/messages/item/${message.id}`, { method: "PATCH", body: JSON.stringify({ content, encryptedContent }) });
    setEditing(null); setMenuId(null); await loadThread();
  };
  const askDeleteMessage = (message, scope) => {
    setMenuId(null);
    setDeleteConfirm({ message, scope });
  };
  const deleteMessage = async () => {
    if (!deleteConfirm) return;
    setDeletingMessage(true);
    try {
      await api(`/api/messages/item/${deleteConfirm.message.id}`, { method: "DELETE", body: JSON.stringify({ scope: deleteConfirm.scope }) });
      setDeleteConfirm(null); setEditing(null); await loadThread(); refreshData?.();
    } finally {
      setDeletingMessage(false);
    }
  };
  const normalizeMeetLink = (raw = "") => {
    const clean = raw.trim();
    return clean ? (/^https?:\/\//i.test(clean) ? clean : `https://${clean}`) : "";
  };
  const extractMeetLink = (text = "") => {
    const match = String(text).match(/https?:\/\/[^\s]+|(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com)[^\s]*/i);
    return match ? normalizeMeetLink(match[0].replace(/[),.]+$/, "")) : "";
  };
  const latestMeetMessage = [...messages].reverse().find((m) => extractMeetLink(m.content));
  const latestMeetLink = latestMeetMessage ? extractMeetLink(latestMeetMessage.content) : "";
  const canEditMeetLink = latestMeetMessage?.sender_id === data.user.id;
  const showMeetInput = !latestMeetLink || (canEditMeetLink && editingMeetLink);
  const openMeet = async (raw, confirmed = false) => {
    const link = meetingUrl(raw);
    if (!link) return setMeetError("Use a valid HTTPS Google Meet, Zoom, Teams, or Jitsi link.");
    if (!activeSession?.id) return setMeetError("Book and accept a session before joining.");
    if (guardianNotice(data.user, activeSession.mode) && !confirmed) return setPendingMeet(link);
    const meetingWindow = window.open("about:blank", "_blank");
    if (!meetingWindow) return setMeetError("Allow pop-ups to open your meeting.");
    meetingWindow.opener = null;
    const joinedSessionId = activeSession.id;
    try {
      await api(`/api/sessions/${joinedSessionId}/join`, { method: "POST", body: JSON.stringify({ guardianConfirmed: confirmed }) });
      meetingWindow.location.replace(link);
      setOpenedMeetSessions((items) => [...new Set([...items, joinedSessionId])]);
      setPendingMeet(null);
      setMeetError("");
      setMeetOpen(false);
      setFinishChecked(false);
      refreshData?.();
    } catch (error) {
      meetingWindow.close();
      setPendingMeet(null);
      return setMeetError(error.message);
    }
    const closeWatcher = setInterval(() => {
      if (!meetingWindow.closed) return;
      clearInterval(closeWatcher);
      meetingWatchers.current.delete(closeWatcher);
      api(`/api/sessions/${joinedSessionId}/leave`, { method: "POST" })
        .then(() => { setFinishChecked(false); setFinishOpen(true); })
        .catch((error) => setMeetError(error.message))
        .finally(() => refreshData?.());
    }, 1000);
    meetingWatchers.current.add(closeWatcher);
  };
  const copyMeetLink = async () => {
    if (!latestMeetLink) return;
    try {
      await navigator.clipboard.writeText(latestMeetLink);
    } catch {
      const input = document.createElement("input");
      input.value = latestMeetLink;
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    setCopiedMeetLink(true);
    setTimeout(() => setCopiedMeetLink(false), 1400);
  };
  const sendMeetLink = async () => {
    const link = meetingUrl(meetLink);
    if (!link) return setMeetError("Use a valid HTTPS Google Meet, Zoom, Teams, or Jitsi link.");
    const content = `Meeting link: ${link}`;
    try {
      const keyResult = await api(`/api/messages/key/${active.id}`);
      const encryptedContent = await buildEncryptedMessage({ senderId: data.user.id, receiverId: active.id, receiverPublicKey: keyResult.publicKey, content });
      if (!encryptedContent) throw new Error("Message encryption is not ready. Ask the other user to open GotTime once, then try again.");
      setMessages([...messages, { id: Date.now(), sender_id: data.user.id, content, created_at: new Date().toISOString() }]);
      await api(`/api/messages/${active.id}`, { method: "POST", body: JSON.stringify({ content, encryptedContent }) });
      await loadThread(active.id);
    }
    catch (error) { alert(error.message || "Could not send encrypted meeting link."); }
    setMeetOpen(false);
    setEditingMeetLink(false);
    setMeetLink("");
  };
  const openScheduleDialog = () => {
    if (!activeSession?.scheduled_for && scheduleStorageKey) localStorage.removeItem(scheduleStorageKey);
    const value = toDateTimeLocalValue(scheduleValueForDialog);
    setScheduleDate(value.split("T")[0] || "");
    setScheduleTime(value.split("T")[1]?.slice(0, 5) || ""); setScheduleTimeTouched(false); setScheduleError("");
    setScheduleOpen(true);
  };
  const scheduledFor = scheduleDate && scheduleTime ? `${scheduleDate}T${scheduleTime}` : "";
  const saveSchedule = async (event) => {
    event.preventDefault();
    if ((!activeSession?.id && !active?.id) || !scheduledFor) return;
    setScheduleError("");
    setScheduleBusy(true);
    try {
      const result = await api(activeSession?.id ? `/api/sessions/${activeSession.id}/schedule` : `/api/messages/${active.id}/schedule`, { method: "POST", body: JSON.stringify({ scheduledFor }) });
      if (scheduleStorageKey) localStorage.setItem(scheduleStorageKey, result?.session?.scheduled_for || scheduledFor);
      setScheduleOpen(false);
      await refreshData?.();
    } catch (error) {
      setScheduleError(error.message || "Could not schedule this session.");
    } finally {
      setScheduleBusy(false);
    }
  };
  const finishSession = async () => {
    if (!activeSession || (!canMentorFinish && !canLearnerConfirm)) return setFinishOpen(false);
    if (!finishChecked) return;
    const result = await sessionAction?.(activeSession.id, canMentorFinish ? "complete" : "confirm", { suppressReward: canLearnerConfirm, suppressRating: true });
    if (canLearnerConfirm && result?.needsRating) {
      setRatingPrompt({ sessionId: activeSession.id, mentorName: active?.username || "your mentor", reward: result.reward });
      setRatingScore(5);
      setRatingFeedback("");
    }
    setFinishChecked(false);
    setFinishOpen(false);
  };
  const closeRating = () => { const shouldReward = ratingPrompt?.reward; setRatingPrompt(null); setRatingFeedback(""); if (shouldReward) showReward?.({ type: "session" }); };
  const submitRating = async (event) => {
    event.preventDefault();
    if (!ratingPrompt) return;
    setRatingBusy(true);
    try {
      await api(`/api/sessions/${ratingPrompt.sessionId}/rating`, { method: "POST", body: JSON.stringify({ score: ratingScore, feedback: ratingFeedback }) });
      await refreshData?.();
      closeRating();
    } finally {
      setRatingBusy(false);
    }
  };
  const addToDraft = (value) => { setDraft((text) => `${text}${text ? " " : ""}${value}`); setToolsOpen(false); };
  const bubbleFor = (m) => {
    const mine = m.sender_id === data.user.id, isEditing = editing?.id === m.id;
    return <div key={m.id} className={`bubble-wrap ${mine ? "mine" : ""}`}>
      <div className="bubble-row">
        <div className="bubble">{isEditing ? <form className="edit-message-form" onSubmit={(e) => { e.preventDefault(); saveEdit(m); }}><input autoFocus value={editing.content} onChange={(e) => setEditing({ ...editing, content: e.target.value })} /><button>Save</button><button type="button" onClick={() => setEditing(null)}>Cancel</button></form> : <>{m.content}{m.edited_at && <em>edited</em>}</>}</div>
        {mine && !isEditing && <div className="message-actions"><button type="button" className="message-more" onClick={() => setMenuId(menuId === m.id ? null : m.id)}><MoreHorizontal size={15} /></button>{menuId === m.id && <div className="message-menu">{!m.is_read && !m.decryptionFailed && <button type="button" onClick={() => setEditing({ id: m.id, content: m.content })}>Edit</button>}<button type="button" onClick={() => askDeleteMessage(m, "me")}>Delete for me</button><button type="button" className="danger" onClick={() => askDeleteMessage(m, "everyone")}>Delete for everyone</button></div>}</div>}
        {!mine && !isEditing && <div className="message-actions"><button type="button" className="message-more" onClick={() => setMenuId(menuId === m.id ? null : m.id)}><MoreHorizontal size={15} /></button>{menuId === m.id && <div className="message-menu"><button type="button" onClick={() => askDeleteMessage(m, "me")}>Delete for me</button></div>}</div>}
      </div>
      <small>{formatTimeIST(m.created_at)}</small>
    </div>;
  };
  return <div className="page messages-page"><div className="messenger"><aside className="contacts"><div className="contact-head"><h3>Conversations</h3></div><div className="contact-search"><Search size={16} /><input placeholder="Search people" /></div>{visibleContacts.map((c, i) => <button key={c.id} className={active?.id === c.id ? "active" : ""} onClick={() => { setActive(c); setMenuId(null); setEditing(null); setToolsOpen(false); }}><Avatar name={c.username} profilePhoto={c.profile_photo} index={i} /><span><b>{c.username}</b><small>{c.last_message || "On GotTime"}</small></span>{Number(c.unread_count || 0) > 0 && <i>{c.unread_count}</i>}</button>)}</aside><section className="thread">{active ? <><header><Avatar name={active.username} profilePhoto={active.profile_photo} /><div><b>{active.username}</b><span><i /> Active now</span></div>{canScheduleSession && <button className="soft-button schedule-top-button" onClick={openScheduleDialog}><CalendarDays size={17} /> Set date & time</button>}<button className="soft-button" onClick={() => setMeetOpen(true)}><Video size={17} /> Meet</button></header><div className="message-stream" onClick={(e) => { if (!e.target.closest(".message-actions")) setMenuId(null); if (!e.target.closest(".composer-tools")) setToolsOpen(false); }}><div className="date-divider"><span>Today</span></div>{messages.length ? messages.map(bubbleFor) : <EmptyState icon={MessageCircle} title="Start with hello" text={`Ask ${active.username} about a skill, session, or idea.`} />}</div><form className="composer" onSubmit={send}><div className="composer-tools"><button type="button" className="icon-button" onClick={() => setToolsOpen(!toolsOpen)}><Plus size={18} /></button>{toolsOpen && <div className="composer-menu"><div><button type="button" onClick={() => addToDraft(":)")}>:)</button><button type="button" onClick={() => addToDraft("fire")}>fire</button><button type="button" onClick={() => addToDraft("+1")}>+1</button><button type="button" onClick={() => addToDraft("*")}>*</button></div><button type="button" onClick={() => addToDraft("star sticker")}>Sticker</button><label>Media files<input type="file" multiple onChange={(e) => { const names = [...e.target.files].map((file) => file.name).join(", "); if (names) addToDraft(`[media: ${names}]`); }} /></label></div>}</div><input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={`Message ${active.username}...`} /><button className="send-button"><Send size={17} /></button></form>{meetOpen && <div className="meet-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setMeetOpen(false)}><form className="meet-dialog" onSubmit={(e) => { e.preventDefault(); sendMeetLink(); }}><button type="button" className="meet-close" onClick={() => setMeetOpen(false)}><X size={16} /></button><span><Video size={20} /></span><h3>Meeting link</h3>{meetError && <div className="form-error" role="alert">{meetError}</div>}{currentUserOpenedMeet && <button type="button" className="soft-button" onClick={async () => { try { await api(`/api/sessions/${activeSession.id}/leave`, { method: "POST" }); setMeetOpen(false); setFinishOpen(true); await refreshData?.(); } catch (error) { setMeetError(error.message); } }}>I have left the meeting</button>}<p>{latestMeetLink ? "Use the shared meeting link for this session." : "Send a Zoom/Google Meet link here. Once shared, the learner can open it from this same Meet button."}</p>{latestMeetLink && <div className="meet-current-link"><span><small>Shared link</small><b>{latestMeetLink}</b></span><button type="button" aria-label="Copy meeting link" onClick={copyMeetLink}>{copiedMeetLink ? <Check size={16} /> : <Clipboard size={16} />}</button></div>}{latestMeetLink && <button type="button" className="meet-link-button" onClick={() => openMeet(latestMeetLink)}>Open meet</button>}{showMeetInput && <input autoFocus value={meetLink} onChange={(e) => setMeetLink(e.target.value)} placeholder={latestMeetLink ? "Paste new meeting link..." : "Paste meeting link to send..."} />}<div><button type="button" className="soft-button" onClick={() => { setMeetOpen(false); setEditingMeetLink(false); setMeetLink(""); }}>Cancel</button>{latestMeetLink && canEditMeetLink && !editingMeetLink && <button type="button" className="primary" onClick={() => { setEditingMeetLink(true); setMeetLink(latestMeetLink); }}>Edit link</button>}{showMeetInput && <button className="primary">{latestMeetLink ? "Update link" : "Send link"}</button>}</div></form></div>}{scheduleOpen && <div className="meet-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !scheduleBusy && setScheduleOpen(false)}><form className="meet-dialog schedule-dialog" onSubmit={saveSchedule}><button type="button" className="meet-close" onClick={() => setScheduleOpen(false)} disabled={scheduleBusy}><X size={16} /></button><span><CalendarDays size={20} /></span><h3>Set session date & time</h3><small className="session-status-pill">{activeSession ? `${activeSession.skill_name || "Skill session"} - ${activeSession.status.replace("_", " ")}` : scheduleValueForDialog ? "Scheduled from chat" : "New session from chat"}</small><p className="schedule-helper">This updates the session in both calendars and creates reminders for 2 days before, 1 day before, 1 hour before, and 5 minutes before.</p><div className="time-range-fields schedule-time-fields"><label>Date<input type="date" required value={scheduleDate} onClick={(e) => e.currentTarget.showPicker?.()} onFocus={(e) => e.currentTarget.showPicker?.()} onChange={(e) => setScheduleDate(e.target.value)} /></label><ThemedTimePicker label="Time" value={scheduleTime} onChange={(value) => { setScheduleTime(value); setScheduleTimeTouched(true); setScheduleError(""); }} /></div>{scheduleError && <div className="guardian-warning"><AlertTriangle size={18} /><span>{scheduleError}</span></div>}<div><button type="button" className="soft-button" disabled={scheduleBusy} onClick={() => setScheduleOpen(false)}>Cancel</button><button className="primary" disabled={scheduleBusy || !scheduledFor}>{scheduleBusy ? "Saving..." : "Save time"}</button></div></form></div>}{finishOpen && <div className="meet-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setFinishOpen(false)}><section className="meet-dialog finish-dialog"><button type="button" className="meet-close" onClick={() => setFinishOpen(false)}><X size={16} /></button><span><Check size={20} /></span><h3>Meeting finished?</h3><p>{finishText}</p>{activeSession && <small className="session-status-pill">Session status: {activeSession.status.replace("_", " ")}</small>}<label className="finish-check"><input type="checkbox" checked={finishChecked} onChange={(e) => setFinishChecked(e.target.checked)} disabled={!canTickFinish} /><span>{canTickFinish ? "I confirm the meeting happened." : "Open the meeting link first to unlock this check."}</span></label><div><button type="button" className="soft-button" onClick={() => { setFinishChecked(false); setFinishOpen(false); }}>{canMentorCheck || canLearnerCheck ? "Not yet" : "Close"}</button>{(canMentorCheck || canLearnerCheck) && <button type="button" className="primary" disabled={!finishChecked || !bothOpenedMeet} onClick={finishSession}>{!bothOpenedMeet ? "Waiting for both sides" : canMentorCheck ? "Submit my check" : "Complete session"}</button>}</div></section></div>}</> : <EmptyState icon={MessageCircle} title="Choose a conversation" text="Your people are waiting." />}</section></div>{pendingMeet && <ConfirmDialog title="Parent or guardian present?" message={guardianNotice(data.user, activeSession?.mode)} confirmLabel="Yes, they are present" onConfirm={() => openMeet(pendingMeet, true)} onClose={() => setPendingMeet(null)} />}{meetError && <div className="form-error" role="alert">{meetError}</div>}{deleteConfirm && <ConfirmDialog title={deleteConfirm.scope === "everyone" ? "Delete for everyone?" : "Delete for you?"} message={deleteConfirm.scope === "everyone" ? "This message will be removed from both sides of the chat." : "This message will only be removed from your chat view."} confirmLabel={deleteConfirm.scope === "everyone" ? "Delete for everyone" : "Delete for me"} danger busy={deletingMessage} onConfirm={deleteMessage} onClose={() => !deletingMessage && setDeleteConfirm(null)} />}{ratingPrompt && <RatingDialog mentorName={ratingPrompt.mentorName} score={ratingScore} setScore={setRatingScore} feedback={ratingFeedback} setFeedback={setRatingFeedback} busy={ratingBusy} onSubmit={submitRating} onSkip={closeRating} />}</div>;
}

function RatingDialog({ mentorName, score, setScore, feedback, setFeedback, busy, onSubmit, onSkip }) {
  return <div className="meet-dialog-backdrop rating-backdrop"><form className="meet-dialog rating-dialog" onSubmit={onSubmit}><span><Star size={20} fill="currentColor" /></span><h3>Rate {mentorName}</h3><p>How was this session? Your rating helps people find great guides.</p><div className="star-rating" role="radiogroup" aria-label="Rate mentor out of 5">{[1, 2, 3, 4, 5].map((value) => <button key={value} type="button" className={value <= score ? "active" : ""} onClick={() => setScore(value)} aria-label={`${value} star${value > 1 ? "s" : ""}`}><Star size={30} fill="currentColor" /></button>)}</div><label className="rating-feedback">Feedback <small>optional</small><textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows="4" placeholder="What went well? Anything they can improve?" /></label><div><button type="button" className="soft-button" onClick={onSkip}>Skip</button><button className="primary" disabled={busy}>{busy ? "Saving..." : "Submit rating"}</button></div></form></div>;
}

function NotificationsPage({ data, setPage, onNotificationClick }) { const updates = [...data.notifications.filter((n) => n.type !== "message" || !n.is_read).map((n) => ({ ...n, source: "notification" })), ...data.transactions.map((t) => ({ ...t, source: "transaction" }))].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)); return <div className="page"><section className="page-title-row"><div><h1>Your <em>updates.</em></h1><p>Only unread messages and recent updates appear here.</p></div><button className="soft-button" onClick={() => setPage("home")}><ArrowRight size={16} /> Back home</button></section><section className="content-card updates-list">{updates.length ? updates.map((item, i) => <button className={`feed-item update-row ${String(item.message || item.note || "").toLowerCase().includes("missed") ? "missed-notification" : ""}`} key={`${item.source}-${item.id || i}`} onClick={() => item.source === "notification" ? onNotificationClick(item) : null}><span className={`feed-icon ${gradients[i % gradients.length]}`}>{item.type === "badge" ? <Award size={17} /> : item.kind === "earned" ? <Coins size={17} /> : item.type === "message" ? <MessageCircle size={17} /> : <Bell size={17} />}</span><div><b>{item.type === "message" && item.sender_name ? `${item.sender_name} sent you a message` : item.message || item.note}</b><small>{formatDate(item.created_at)}</small></div></button>) : <EmptyState icon={Bell} title="All clear" text="No unread notifications or recent credit updates yet." />}</section></div>; }
function SubscriptionPage({ data, buyCredits, setPage }) {
  const packs = [
    { id: "starter", name: "Starter top-up", credits: 5, price: 99, note: "Good for a few quick sessions", tone: "mint" },
    { id: "boost", name: "Skill boost", credits: 12, price: 199, note: "Best for active learners", tone: "violet", popular: true },
    { id: "pro", name: "Deep learner", credits: 30, price: 449, note: "For project-style learning", tone: "peach" },
  ];
  return <div className="page subscription-page"><section className="subscription-hero"><div><span className="eyebrow"><Coins size={15} /> Credit wallet</span><h1>Ran out of credits?<br /><em>Top up and keep learning.</em></h1><p>Buy credit packs securely with UPI, cards, wallets, or netbanking, then spend credits to book mentors.</p><div className="wallet-chip"><Coins size={18} /><span><b>{data.user.credits || 0}</b> credits available</span></div></div><button className="soft-button" onClick={() => setPage("discover")}>Browse mentors <ArrowRight size={16} /></button></section><section className="subscription-grid">{packs.map((pack) => <article className={`subscription-card card-${pack.tone} ${pack.popular ? "popular" : ""}`} key={pack.id}>{pack.popular && <span className="popular-pill">Most useful</span>}<div className="subscription-icon"><Coins size={24} /></div><h3>{pack.name}</h3><p>{pack.note}</p><div className="subscription-price"><b>{"\u20B9"}{pack.price}</b><span>{pack.credits} credits</span></div><small>About {pack.credits} one-hour mentor sessions</small><button className="primary full" onClick={() => buyCredits(pack.id)}>Buy {pack.credits} credits <ArrowRight size={17} /></button></article>)}</section><section className="content-card subscription-note"><div><h3>How credit packs work</h3><p>Credits are used inside GotTime to book sessions. You can still earn credits by teaching; buying packs is just a quick top-up when you want to keep learning.</p></div><div className="tag-row"><span>1 credit = 1 session</span><span>Earn by teaching</span><span>Spend by learning</span></div></section></div>;
}
const requestDonationUpi = (signal) => api("/api/donations/upi", { signal });
function DonationPage({ donateSupport, setPage }) {
  const [customAmount, setCustomAmount] = useState("");
  const amounts = [50, 100, 250];
  const customValue = Number(customAmount || 0);
  return <div className="page donation-page"><section className="subscription-hero donation-hero"><div><span className="eyebrow"><Sparkles size={15} /> Public donation</span><h1>Support GotTime.<br /><em>Keep the platform running.</em></h1><p>Donations help cover servers, database maintenance, and future improvements.</p></div><button className="soft-button" onClick={() => setPage("about")}>Why GotTime exists <ArrowRight size={16} /></button></section><section className="content-card donation-panel"><div className="donation-panel-head"><span className="subscription-icon"><Sparkles size={24} /></span><div><h3>Pick an amount</h3><p>Choose a quick amount or enter your own.</p></div></div><div className="quick-donation-row">{amounts.map((amount) => <button type="button" className="quick-donation-button" key={amount} onClick={() => donateSupport(amount)}><span>{"\u20B9"}{amount}</span><small>Support with {"\u20B9"}{amount}</small><ArrowRight size={17} /></button>)}</div><div className="custom-donation-row"><label className="custom-donation-input"><span>Custom amount</span><div><b>{"\u20B9"}</b><input type="text" inputMode="numeric" pattern="[0-9]*" value={customAmount} onChange={(e) => setCustomAmount(e.target.value.replace(/\D/g, ""))} placeholder="Enter amount" /></div></label><button className="primary" disabled={customValue < 10} onClick={() => donateSupport(customValue)}>Support GotTime <ArrowRight size={17} /></button></div></section><DonationUpiPanel request={requestDonationUpi} /><section className="content-card subscription-note"><div><h3>Where donations help</h3><p>Donations help cover servers, database maintenance, and future improvements.</p></div><div className="tag-row"><span>Servers</span><span>Database maintenance</span><span>Future improvements</span></div></section></div>;
}
function TestimonialsPage({ data, setPage }) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [saved, setSaved] = useState(false);
  const [feedback, setFeedback] = useState({ message: "" });
  const stories = [
    { name: "Ananya", quote: "I used one credit for a design critique and fixed my portfolio the same day.", tag: "Design" },
    { name: "Rohit", quote: "Teaching Python for an hour earned me enough credits to learn music production.", tag: "Programming" },
    { name: "Meera", quote: "GotTime feels less transactional. It is just people helping people get sharper.", tag: "Skill swap" },
    { name: "JJD", quote: "The chat-to-book flow made it easy to ask questions before scheduling.", tag: "Mentorship" },
  ];
  const submitFeedback = (event) => {
    event.preventDefault();
    const item = { name: data.user.username, email: data.user.email, message: feedback.message, userId: data.user.id, createdAt: new Date().toISOString() };
    const stored = JSON.parse(localStorage.getItem("gt-feedback") || "[]");
    localStorage.setItem("gt-feedback", JSON.stringify([item, ...stored].slice(0, 20)));
    setFeedback({ message: "" });
    setSaved(true);
    setFeedbackOpen(false);
  };
  return <div className="page testimonials-page"><section className="testimonial-hero"><span className="eyebrow"><Star size={15} /> Community stories</span><h1>Real people, real <em>progress.</em></h1><p>Short wins from people trading time on GotTime.</p><button className="primary" onClick={() => setPage("discover")}>Find your mentor <ArrowRight size={17} /></button></section><section className="testimonial-grid">{stories.map((story, i) => <article className={`testimonial-card card-${gradients[i % gradients.length]}`} key={story.name}><div><Avatar name={story.name} index={i} /><span><b>{story.name}</b><small>{story.tag}</small></span></div><p>"{story.quote}"</p><small>{memberLabel()}</small></article>)}</section><section className="content-card testimonial-cta"><div><h3>Have a GotTime story?</h3><p>Send feedback or a testimonial. Good product taste comes from listening.</p>{saved && <small className="feedback-saved">Saved. Thank you for the story.</small>}</div><button type="button" className="soft-button" onClick={() => setFeedbackOpen(true)}>Share feedback</button></section>{feedbackOpen && <Modal title="Share feedback" subtitle={`Signed in as ${data.user.username}. Just tell us what worked, or what felt rough.`} onClose={() => setFeedbackOpen(false)}><form className="modal-form" onSubmit={submitFeedback}><label>Your feedback<textarea required rows="5" value={feedback.message} onChange={(e) => setFeedback({ message: e.target.value })} placeholder="What should GotTime improve?" /></label><button className="primary full">Send feedback <ArrowRight size={17} /></button></form></Modal>}</div>;
}

function AboutPage({ data, setPage }) {
  const values = [
    ["Time over money", "GotTime is built around exchanging useful hours, not charging for access."],
    ["Real people first", "Chat, book, meet, and confirm sessions with humans who are one chapter ahead."],
    ["Safety by design", "Age checks, meeting confirmation, ratings, and profiles help keep learning grounded."],
  ];
  const seenMentors = new Set();
  const mentorPeople = [];
  (data.mentors || []).forEach((mentor) => {
    if (mentorPeople.length >= 3 || seenMentors.has(mentor.id)) return;
    seenMentors.add(mentor.id);
    const skillName = mentor.primary_skill || (mentor.skills || "").split(",").map((skill) => skill.trim()).filter(Boolean)[0] || "GotTime";
    const icon = String(skillName || mentor.username || "GT").replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "GT";
    mentorPeople.push({
      id: mentor.id,
      name: mentor.username || "GotTime mentor",
      role: `${skillName} mentor`,
      kind: ["palette", "code", "sound"][mentorPeople.length],
      icon,
      photo: mentor.profile_photo || "",
    });
  });
  const people = mentorPeople.length ? mentorPeople : [
    { id: "no-mentor-1", name: "Add a skill", role: "Mentors will appear here", kind: "palette", icon: "+" },
    { id: "no-mentor-2", name: "Share knowledge", role: "Create the first listing", kind: "code", icon: "SK" },
    { id: "no-mentor-3", name: "Start trading", role: "Build the mentor list", kind: "sound", icon: "GT" },
  ];
  return <div className="page about-page"><section className="about-hero"><div className="about-hero-copy"><span className="eyebrow"><ShieldCheck size={15} /> About GotTime</span><h1>Learning should feel human, useful, and <em>earned.</em></h1><p>GotTime is a skill exchange where people trade time credits for mentorship. Teach something you know, earn credits, and spend them learning something new.</p><div className="about-hero-actions"><button className="primary" onClick={() => setPage("discover")}>Browse mentors <ArrowRight size={17} /></button><button className="soft-button" onClick={() => setPage("skills")}>Share a skill <Plus size={17} /></button></div></div><div className="about-people-strip">{people.map((person, i) => <article className={"about-person " + person.kind} key={person.id}><Avatar name={person.name} profilePhoto={person.photo} index={i} /><div><strong>{person.name}</strong><small>{person.role}</small></div><em>{person.icon}</em></article>)}</div></section><section className="about-grid">{values.map(([title, text], i) => <article className={`about-card card-${gradients[i]}`} key={title}><span>{i === 0 ? <Clock3 size={24} /> : i === 1 ? <Users size={24} /> : <ShieldCheck size={24} />}</span><h3>{title}</h3><p>{text}</p></article>)}</section><section className="content-card about-story"><div><h3>Why GotTime exists</h3><p>Most people have something valuable to teach, but not everyone wants another paid course or transactional marketplace. GotTime turns everyday knowledge into a fair exchange: one hour taught becomes one hour learned.</p></div><div><h3>How it works</h3><p>Create a profile, add skills you can share, browse mentors, chat before booking, meet online or offline, then confirm the session to unlock credits, XP, badges, and progress.</p></div></section></div>;
}
function Leaderboard({ data, onOpenProfile }) { const leaders = data.leaderboard || []; const podiumOrder = leaders.length <= 1 ? leaders.map((p, index) => ({ p, rank: index + 1, place: 1 })) : [leaders[1] && { p: leaders[1], rank: 2, place: 0 }, leaders[0] && { p: leaders[0], rank: 1, place: 1 }, leaders[2] && { p: leaders[2], rank: 3, place: 2 }].filter(Boolean); return <div className="page"><section className="leader-hero"><span className="eyebrow"><Trophy size={15} /> Time legends</span><h1>Kindness looks good<br />on the <em>leaderboard.</em></h1><p>Celebrate the people giving their time and curiosity.</p></section><section className="podium">{podiumOrder.map(({ p, rank, place }) => <button type="button" className={`podium-person place-${place}`} key={p.id} onClick={() => onOpenProfile(p)}><span className="crown">{rank}</span><Avatar name={p.username} profilePhoto={p.profile_photo} size="lg" index={place + 1} /><h3>{p.username}</h3><span>{p.sessions_completed || 0} sessions</span><div><Coins size={17} /> {p.credits} hours</div><i /></button>)}</section><section className="content-card rank-list">{leaders.length ? leaders.map((p, index) => <button type="button" className={`rank-row ${p.id === data.user.id ? "me" : ""}`} key={p.id} onClick={() => onOpenProfile(p)}><b>#{index + 1}</b><Avatar name={p.username} profilePhoto={p.profile_photo} index={index + 3} /><span><strong>{p.username} {p.id === data.user.id && "(you)"}</strong><small>{p.sessions_completed} sessions</small></span><div><Coins size={16} /> {p.credits}h</div></button>) : <EmptyState icon={Trophy} title="No rankings yet" text="Complete sessions to appear here." />}</section></div>; }
function BadgeVault({ data }) {
  const vault = buildBadgeVault(data);
  const unlocked = vault.filter((badge) => badge.unlocked).length;
  return <div className="page badge-vault-page"><section className="badge-vault-hero"><div><span className="eyebrow"><LockKeyhole size={15} /> Achievement vault</span><h1>Unlock badges like <em>loot.</em></h1><p>Collect real badge drops as you learn, teach, and trade time.</p></div><div className="vault-score"><Award size={24} /><b>{unlocked}/{vault.length}</b><span>badges unlocked</span></div></section><section className="badge-vault-grid">{vault.map(({ icon: Icon, ...badge }) => <article key={badge.name} className={`vault-badge-card ${badge.unlocked ? "unlocked" : "locked"} ${badge.color} shape-${badge.shape}`}><span className="vault-lock">{badge.unlocked ? <Check size={16} /> : <LockKeyhole size={16} />}</span><div className="vault-badge-stage"><div className="vault-badge-medal"><span className="vault-medal-ring"><Icon size={32} /></span><i /><i /></div></div><span className="vault-tier">{badge.tier}</span><h3>{badge.name}</h3><p>{badge.condition}</p><div className="vault-progress-row"><span>{badge.unlocked ? "Unlocked" : `${Math.min(badge.value, badge.target)} / ${badge.target}`}</span></div><div className="progress vault-progress"><span style={{ width: `${badge.unlocked ? 100 : badge.progress}%` }} /></div></article>)}</section></div>;
}

function Profile({ data, saveProfile, uploadPhoto, profileUser, setPage }) {
  const viewedUser = refreshProfileStats(profileUser || data.user, data), isOwn = viewedUser.id === data.user.id;
  const [editing, setEditing] = useState(false), [form, setForm] = useState(data.user), [photoBusy, setPhotoBusy] = useState(false), [photoMenu, setPhotoMenu] = useState(false);
  useEffect(() => setForm(data.user), [data.user]);
  const save = async () => { await saveProfile(form); setEditing(false); };
  const badges = isOwn ? data.badges : (viewedUser.badges || []);
  const earnedBadgeNames = new Set(badges.map((badge) => badge.name));
  const badgeDisplay = buildBadgeVault({ ...data, user: viewedUser, badges }).filter((badge) => earnedBadgeNames.has(badge.name));
  const choosePhoto = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setPhotoBusy(true);
    setPhotoMenu(false);
    try { await uploadPhoto(file); } finally { setPhotoBusy(false); }
  };
  return <div className="page"><section className="profile-hero"><div className="profile-pattern" /><Avatar name={viewedUser.username} profilePhoto={viewedUser.profile_photo} size="xl" /><div className="profile-title"><span className="eyebrow"><ShieldCheck size={15} /> {isOwn ? "Verified time trader" : "Leaderboard profile"}</span><h1>{viewedUser.username}</h1><p>{cleanBio(viewedUser.bio) || (isOwn ? "" : "A GotTime member and time trader.")}</p><div className="tag-row">{isOwn && <span>{viewedUser.location || "Private location"}</span>}{!isOwn && <span>GotTime member</span>}{viewedUser.created_at && <span>Member since {formatDate(viewedUser.created_at, { year: "numeric" })}</span>}</div></div>{isOwn && <button className="soft-button" onClick={() => setEditing(true)}><Pencil size={16} /> Edit profile</button>}</section><section className="profile-grid"><div className="content-card"><div className="card-head"><div><h3>Impact snapshot</h3><p>Good time, well traded</p></div></div><div className="impact-grid"><div><Coins size={21} /><b>{viewedUser.credits || 0}h</b><span>balance</span></div><div><CalendarDays size={21} /><b>{viewedUser.sessions_completed || 0}</b><span>sessions</span></div><div><Star size={21} /><b>{formatRating(viewedUser.rating)}</b><span>rating</span></div><div><Flame size={21} /><b>{viewedUser.streak || 0}</b><span>day streak</span></div></div></div><div className="content-card badge-cabinet"><div className="card-head badge-cabinet-head"><div><h3>Badge vault</h3><p>Locked and unlocked rewards</p></div>{isOwn && <button className="mini-button" onClick={() => setPage("badges")}>View vault</button>}</div>{badgeDisplay.length ? <div className="badge-row profile-badge-row">{badgeDisplay.map(({ icon: Icon, ...badge }) => <div key={badge.name} className={`profile-vault-badge ${badge.color} shape-${badge.shape}`}><div className="vault-badge-medal"><span className="vault-medal-ring"><Icon size={25} /></span><i /><i /></div><b>{badge.name}</b></div>)}</div> : <EmptyState icon={Award} title="No badges yet" text="Complete sessions to earn badges." />}</div></section>{editing && <Modal title="Polish your profile" subtitle="Help people know the human behind the skill." onClose={() => setEditing(false)}><div className="profile-edit-avatar"><Avatar name={data.user.username} profilePhoto={data.user.profile_photo} size="xl" /><button type="button" className="photo-edit-dot" onClick={() => setPhotoMenu(!photoMenu)} disabled={photoBusy} aria-label="Change profile photo"><Pencil size={15} /></button>{photoMenu && <div className="photo-choice-menu"><label>Gallery / files<input type="file" accept="image/*" disabled={photoBusy} onChange={choosePhoto} /></label><label>Camera<input type="file" accept="image/*" capture="environment" disabled={photoBusy} onChange={choosePhoto} /></label></div>}</div><div className="form-grid"><label>Name<input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label><label>Location <small>private</small><input value={form.location || ""} onChange={(e) => setForm({ ...form, location: e.target.value })} /></label><label className="wide">Your story<textarea value={form.bio || ""} onChange={(e) => setForm({ ...form, bio: e.target.value })} rows="4" /></label></div><button className="primary full" onClick={save}>Save changes <Check size={17} /></button></Modal>}</div>;
}
function Modal({ title, subtitle, onClose, children }) { return <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}><section className="modal-card"><button className="icon-button modal-close" onClick={onClose}><X size={18} /></button><div className="modal-title"><span><Sparkles size={19} /></span><div><h2>{title}</h2><p>{subtitle}</p></div></div>{children}</section></div>; }
function blankQuizQuestion() { return { question: "", options: ["", "", "", ""], correct: "A" }; }
function QuizModal({ quiz, onClose, onSave, onSubmit }) {
  const isBuilder = quiz.mode === "build";
  const existingQuestions = quiz.questions?.length ? quiz.questions : Array.from({ length: 5 }, blankQuizQuestion);
  const [questions, setQuestions] = useState(existingQuestions.map((q) => ({ question: q.question || "", options: q.options?.length === 4 ? q.options : ["", "", "", ""], correct: q.correct || "A" })));
  const [answers, setAnswers] = useState(quiz.attempt?.answers || {});
  const [busy, setBusy] = useState(false);
  const updateQuestion = (index, patch) => setQuestions((items) => items.map((q, i) => i === index ? { ...q, ...patch } : q));
  const updateOption = (qIndex, optionIndex, value) => updateQuestion(qIndex, { options: questions[qIndex].options.map((option, i) => i === optionIndex ? value : option) });
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { await onSave(quiz.session.id, questions); } catch { /* Parent displays the API error. */ } finally { setBusy(false); }
  };
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { await onSubmit(quiz.session.id, answers); } catch { /* Parent displays the API error. */ } finally { setBusy(false); }
  };
  if (!isBuilder) {
    const attempted = Boolean(quiz.attempt);
    const displayedAnswers = attempted ? quiz.attempt.answers || {} : answers;
    return <Modal title={attempted ? "Quiz result" : "Session quiz"} subtitle={attempted ? `Score: ${quiz.attempt.score}/${quiz.attempt.total}` : "Answer the MCQs your mentor attached after the session."} onClose={onClose}>
      <form className="quiz-form" onSubmit={submit}>
        {quiz.questions?.length ? quiz.questions.map((q, index) => <article className="quiz-question-card" key={q.id || index}>
          <b>{index + 1}. {q.question}</b>
          <div className="quiz-option-list">{q.options.map((option, optionIndex) => {
            const letter = "ABCD"[optionIndex];
            const state = quizOptionState(attempted, displayedAnswers[q.id], q.correct, letter);
            return <label key={letter} className={state}><input type="radio" name={`q-${q.id}`} value={letter} disabled={attempted || !quiz.canSubmit} checked={displayedAnswers[q.id] === letter} onChange={() => setAnswers({ ...answers, [q.id]: letter })} /> <span>{letter}. {option}</span>{state && <span className="quiz-answer-feedback">{state === "incorrect" ? <X size={15} aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}{state === "correct" ? "Your answer · Correct" : state === "incorrect" ? "Your answer · Incorrect" : "Correct answer"}</span>}</label>;
          })}</div>
        </article>) : <EmptyState icon={Clipboard} title="No quiz attached yet" text="The mentor has not added MCQs for this session." />}
        {!attempted && quiz.canSubmit && <button className="primary full" disabled={busy || quiz.questions.some((q) => !answers[q.id])}>{busy ? "Submitting..." : "Submit quiz"} <ArrowRight size={17} /></button>}
        {!attempted && !quiz.canSubmit && <div className="guardian-warning"><AlertTriangle size={18} /><span>The quiz unlocks at the end of the session.</span></div>}
      </form>
    </Modal>;
  }
  return <Modal title={quiz.questions?.length ? "Edit session quiz" : "Create session quiz"} subtitle="Attach 5 to 10 MCQ questions for the learner to answer after the session." onClose={onClose}>
    <form className="quiz-form" onSubmit={save}>
      {questions.map((q, index) => <article className="quiz-question-card" key={index}>
        <div className="quiz-question-head"><b>Question {index + 1}</b>{questions.length > 5 && <button type="button" className="text-button" onClick={() => setQuestions(questions.filter((_, i) => i !== index))}>Remove</button>}</div>
        <label>Question<input required value={q.question} onChange={(e) => updateQuestion(index, { question: e.target.value })} placeholder="Example: What is the first step?" /></label>
        <div className="quiz-options-grid">{q.options.map((option, optionIndex) => <label key={optionIndex}>Option {"ABCD"[optionIndex]}<input required value={option} onChange={(e) => updateOption(index, optionIndex, e.target.value)} /></label>)}</div>
        <label>Correct answer<select value={q.correct} onChange={(e) => updateQuestion(index, { correct: e.target.value })}>{["A", "B", "C", "D"].map((letter) => <option key={letter} value={letter}>Option {letter}</option>)}</select></label>
      </article>)}
      <div className="quiz-actions"><button type="button" className="soft-button" disabled={questions.length >= 10} onClick={() => setQuestions([...questions, blankQuizQuestion()])}><Plus size={16} /> Add question</button><span>{questions.length}/10 questions</span></div>
      <button className="primary full" disabled={busy}>{busy ? "Saving..." : "Attach quiz"} <Clipboard size={17} /></button>
    </form>
  </Modal>;
}
function ThemedTimePicker({ label, value, onChange, min = "", max = "", showHint = true }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(clockPartsFromTime(value || min || "17:00"));
  const draftValue = timeFromClockParts(draft.hour, draft.minute, draft.period);
  const isValidTime = Boolean(draftValue);
  const inRange = isValidTime && (!min || !max || isDateTimeInsidePreferredRange(`2026-10-09T${draftValue}`, `${min}-${max}`));
  const applyTime = () => {
    if (!isValidTime || !inRange) return;
    onChange(draftValue);
    setOpen(false);
  };
  return <label className="pretty-time-field">{label}<button type="button" className={`pretty-time-trigger ${open ? "open" : ""}`} onClick={() => { const next = clockPartsFromTime(value || min || "17:00"); setDraft({ ...next, period: next.period || defaultPeriodForRange(min, max) }); setOpen(!open); }}><span>{value ? formatClock(value) : "Choose time"}</span><Clock3 size={16} /></button>{open && <div className="pretty-time-menu exact-time-menu dropdown-time-menu"><div className="dropdown-time-row"><select aria-label="Hour" value={draft.hour} onChange={(e) => setDraft({ ...draft, hour: e.target.value })}>{Array.from({ length: 12 }, (_, index) => String(index + 1)).map((hour) => <option key={hour} value={hour}>{hour.padStart(2, "0")}</option>)}</select><b>:</b><select aria-label="Minute" value={draft.minute} onChange={(e) => setDraft({ ...draft, minute: e.target.value })}>{Array.from({ length: 60 }, (_, index) => String(index).padStart(2, "0")).map((minute) => <option key={minute} value={minute}>{minute}</option>)}</select><button type="button" className={draft.period === "AM" ? "active" : ""} onClick={() => setDraft({ ...draft, period: "AM" })}>AM</button><button type="button" className={draft.period === "PM" ? "active" : ""} onClick={() => setDraft({ ...draft, period: "PM" })}>PM</button></div>{showHint && <small>{min && max ? `Choose any time from ${formatClock(min)} to ${formatClock(max)}.` : "Choose a time."}</small>}{isValidTime && !inRange && <small className="time-range-error">That time is outside the allowed range.</small>}<button type="button" className="exact-time-apply" disabled={!isValidTime || !inRange} onClick={applyTime}>{inRange ? `Use ${formatClock(draftValue)}` : "Choose a valid time"}</button></div>}</label>;
}
function BookingModal({ data, mentor, onClose, onBooked }) {
  const userAge = Number(data.user?.age || 0);
  const inferredMode = mentor?.availability === "offline" ? "offline" : "online";
  const [form] = useState({ mentorId: mentor?.id || data.mentors[0]?.id, skillId: mentor?.skill_id || data.skills[0]?.id, mode: inferredMode, notes: "" });
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedTime, setSelectedTime] = useState("");
  const [availability, setAvailability] = useState(null);
  const [availabilityRetry, setAvailabilityRetry] = useState(0);
  const [guardianConfirmed, setGuardianConfirmed] = useState(false);
  const guardianNotice = form.mode === "offline" && userAge < 16
    ? "Because you are under 16, offline sessions must be attended with a parent or guardian."
    : form.mode === "online" && userAge < 13
      ? "Because you are under 13, online sessions must be attended with a parent or guardian."
      : "";
  const guardianRequired = Boolean(guardianNotice);
  const guardianConfirmText = form.mode === "offline" ? "I confirm that my parent or guardian will accompany me for this offline session." : "I confirm that my parent or guardian will be present during this online session.";
  const preferredRange = parsePreferredTimeRange(mentor?.preferred_time);
  const scheduledFor = selectedDate && selectedTime ? `${selectedDate}T${selectedTime}` : "";
  const inRange = isDateTimeInsidePreferredRange(scheduledFor, mentor?.preferred_time);
  const availabilityKey = JSON.stringify([form.mentorId, form.skillId, scheduledFor, availabilityRetry]);
  const checked = availability?.key === availabilityKey;
  const checking = Boolean(scheduledFor && inRange && !checked);
  useEffect(() => {
    setAvailability(null);
    if (!scheduledFor || !inRange) return;
    return checkBookingAvailability(
      (signal) => api("/api/sessions/availability", { method: "POST", signal, body: JSON.stringify({ mentorId: form.mentorId, skillId: form.skillId, scheduledFor }) }),
      (result) => setAvailability({ ...result, key: availabilityKey }),
    );
  }, [availabilityKey, inRange]);
  const canBook = Boolean(scheduledFor && inRange && checked && availability.available && (!guardianRequired || guardianConfirmed));
  return <Modal title="Book session" subtitle="Pick a time inside the mentor's available range." onClose={onClose}><form onSubmit={(e) => { e.preventDefault(); if (canBook) onBooked({ ...form, scheduledFor, guardianNotice, guardianConfirmed: guardianRequired ? guardianConfirmed : false }); }} className="modal-form"><div className="booking-preferred-time"><Clock3 size={18} /><span><b>Available time range</b><small>{preferredTimeLabel(mentor?.preferred_time)}</small></span></div><div className="time-range-fields"><label>Date<input type="date" required value={selectedDate} onClick={(e) => e.currentTarget.showPicker?.()} onFocus={(e) => e.currentTarget.showPicker?.()} onChange={(e) => setSelectedDate(e.target.value)} /></label><ThemedTimePicker label="Your time" value={selectedTime} min={preferredRange.start} max={preferredRange.end} onChange={setSelectedTime} /></div>{scheduledFor && !inRange && <div className="guardian-warning"><AlertTriangle size={18} /><span>Choose a time within {preferredTimeLabel(mentor?.preferred_time)}.</span></div>}{scheduledFor && inRange && <div className={`booking-availability ${checked && !availability.available ? "conflict" : ""}`} role="status" aria-live="polite">{checking ? <><Clock3 size={18} /><span>Checking for session clashes…</span></> : checked && !availability.available ? <><AlertTriangle size={18} /><span>{availability.message || "This time is unavailable. Choose another time."}{availability.error && <button type="button" className="text-button" onClick={() => setAvailabilityRetry((value) => value + 1)}>Check again</button>}</span></> : <><Check size={18} /><span>No session clashes for this time.</span></>}</div>}{guardianNotice && <div className="guardian-warning"><AlertTriangle size={18} /><span>{guardianNotice}</span></div>}{guardianRequired && <label className="guardian-confirm"><input type="checkbox" checked={guardianConfirmed} onChange={(e) => setGuardianConfirmed(e.target.checked)} /><span>{guardianConfirmText}</span></label>}<button className="primary full" disabled={!canBook}>Send booking request <ArrowRight size={17} /></button></form></Modal>;
}
function SkillModal({ onClose, onAdded, initialSkill }) {
  const knownCategories = Object.keys(categoryEmoji);
  const initialCategory = initialSkill?.category && knownCategories.includes(initialSkill.category) ? initialSkill.category : initialSkill?.category ? "Other" : "Design";
  const [form, setForm] = useState({
    id: initialSkill?.id || null,
    skill_id: initialSkill?.skill_id || null,
    name: initialSkill?.name || initialSkill?.skill_name || "",
    category: initialCategory,
    customCategory: initialCategory === "Other" ? initialSkill?.category || "" : "",
    description: initialSkill?.description || "",
    duration: initialSkill?.duration || 60,
    availability: initialSkill?.availability || "online",
    preferred_time: initialSkill?.preferred_time || "",
  });
  const initialRange = parsePreferredTimeRange(initialSkill?.preferred_time || "");
  const [timeRange, setTimeRange] = useState({ start: initialRange.start, end: initialRange.end });
  const [categoryOpen, setCategoryOpen] = useState(false);
  const categories = knownCategories;
  const isEditing = Boolean(initialSkill);
  const submit = (e) => {
    e.preventDefault();
    const category = form.category === "Other" ? form.customCategory.trim() : form.category;
    if (!category) return;
    const preferred_time = timeRange.start && timeRange.end ? `${timeRange.start}-${timeRange.end}` : "";
    onAdded({ ...form, category, preferred_time });
  };
  return <Modal title={isEditing ? "Edit your skill" : "Share what you know"} subtitle={isEditing ? "Update the skill name, category, mode, or pitch." : "You don't need to be world-class-just one chapter ahead."} onClose={onClose}><form className="modal-form" onSubmit={submit}><label>Skill name<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label><label>Category<div className={`custom-select ${categoryOpen ? "open" : ""}`}><button type="button" className="custom-select-trigger" onClick={() => setCategoryOpen(!categoryOpen)}><span>{form.category === "Other" && form.customCategory.trim() ? form.customCategory.trim() : form.category}</span><ChevronDown size={18} /></button>{categoryOpen && <div className="custom-select-menu">{categories.map((c) => <button type="button" key={c} className={form.category === c ? "active" : ""} onClick={() => { setForm({ ...form, category: c }); setCategoryOpen(false); }}>{categoryEmoji[c] && <i>{categoryEmoji[c]}</i>}<span>{c}</span>{form.category === c && <Check size={15} />}</button>)}</div>}</div></label>{form.category === "Other" && <label>Custom category<input required autoFocus value={form.customCategory} onChange={(e) => setForm({ ...form, customCategory: e.target.value })} placeholder="Example: Video editing" /></label>}<label>Preferred session mode<div className="mode-pick skill-mode-pick"><button type="button" className={form.availability === "online" ? "active" : ""} onClick={() => setForm({ ...form, availability: "online" })}><Video size={16} /> Online</button><button type="button" className={form.availability === "offline" ? "active" : ""} onClick={() => setForm({ ...form, availability: "offline" })}><Users size={16} /> Offline</button><button type="button" className={form.availability === "both" ? "active" : ""} onClick={() => setForm({ ...form, availability: "both" })}><Sparkles size={16} /> Both</button></div></label><div className="time-range-fields"><ThemedTimePicker showHint={false} label="Available from" value={timeRange.start} onChange={(value) => setTimeRange({ ...timeRange, start: value })} /><ThemedTimePicker showHint={false} label="Available until" value={timeRange.end} onChange={(value) => setTimeRange({ ...timeRange, end: value })} /></div><label>Short pitch<textarea rows="3" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label><button className="primary full">{isEditing ? "Save skill" : <>Add to my skill deck <Plus size={17} /></>}</button></form></Modal>;
}
function Reward({ reward, onClose }) {
  if (reward?.type === "questXp") {
    return <div className="reward-backdrop">
      <section className="reward-card" role="dialog" aria-modal="true" aria-label="Daily quest complete">
        <div className="reward-burst"><Zap size={46} /></div>
        <span className="eyebrow">Daily quest complete</span>
        <h2>+{reward.xp} bonus XP!</h2>
        <p>{reward.name} completed. Your XP has been added to your total and counts toward XP milestone badges.</p>
        {reward.badges?.length > 0 && <div className="reward-points">{reward.badges.map((name) => <div key={name}><Award size={24} /><b>{name}</b><span>Badge unlocked · saved to your vault</span></div>)}</div>}
        <button className="primary full" onClick={onClose}>Keep going <ArrowRight size={17} /></button>
      </section>
    </div>;
  }
  const isXpBadge = reward?.type === "xpBadge";
  const isSkillBadge = reward?.type === "skillBadge";
  const isLootBadge = isXpBadge || isSkillBadge;
  return <div className={`reward-backdrop loot-backdrop ${isLootBadge ? "xp-loot-backdrop" : ""}`}>
    <div className="loot-rays">{Array.from({ length: 12 }).map((_, i) => <i key={i} style={{ "--i": i }} />)}</div>
    <div className="loot-confetti">{Array.from({ length: isLootBadge ? 34 : 18 }).map((_, i) => <i key={i} style={{ "--i": i }} />)}</div>
    <section className={`reward-card loot-card ${isLootBadge ? "xp-loot-card" : ""}`}>
      <div className="loot-rings"><span /><span /><span /></div>
      <div className={`reward-burst loot-medal ${isLootBadge ? "xp-loot-medal" : ""}`}>
        {isXpBadge ? <Zap size={46} /> : isSkillBadge ? <Sparkles size={46} /> : <Trophy size={42} />}
        <Sparkles className="loot-spark spark-one" size={18} /><Sparkles className="loot-spark spark-two" size={16} />
      </div>
      <span className="eyebrow">{isXpBadge ? "XP badge unlocked" : isSkillBadge ? "Skill badge unlocked" : "Rare badge unlocked"}</span>
      <h2>{isXpBadge ? `${reward.name || "Curiosity Spark"} unlocked!` : isSkillBadge ? `${reward.name || "Skill Sharer"} unlocked!` : "Session mastered!"}</h2>
      <p>{isXpBadge ? "You crossed 50 total XP. Your first XP badge just dropped into the vault." : isSkillBadge ? "Your first teachable skill is live. The Skill Sharer badge just dropped into your vault." : "Both sides confirmed the session. Your credits, badges, and achievements have been added to your profile."}</p>
      <div className="reward-points loot-rewards">
        <div><Award size={24} /><b>{isXpBadge ? "Curiosity Spark" : isSkillBadge ? "Skill Sharer" : "New badge"}</b><span>{isXpBadge ? "First XP badge" : isSkillBadge ? "Teaching badge" : "Achievement earned"}</span></div>
        <div>{isXpBadge ? <Zap size={24} /> : isSkillBadge ? <BookOpen size={24} /> : <Coins size={24} />}<b>{isXpBadge ? "50 XP milestone" : isSkillBadge ? "Skill live" : "Credits updated"}</b><span>{isXpBadge ? "Total XP" : isSkillBadge ? "Now discoverable" : "Time trade complete"}</span></div>
      </div>
      <button className="primary full receive-reward" onClick={onClose}>{isLootBadge ? "Claim badge" : "Receive reward"} <Sparkles size={17} /></button>
    </section>
  </div>;
}
function Toast({ message, error }) { return <div className={`toast ${error ? "toast-error" : ""}`}><span>{error ? <X size={16} /> : <Check size={16} />}</span>{message}</div>; }
function ConfirmDialog({ title = "Are you sure?", message, confirmLabel = "Yes, remove it", danger = false, busy = false, onConfirm, onClose }) {
  return <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}><section className="modal-card confirm-card"><button className="icon-button modal-close" onClick={onClose} disabled={busy}><X size={18} /></button><div className="modal-title"><span><AlertTriangle size={19} /></span><div><h2>{title}</h2><p>{message}</p></div></div><div className="confirm-actions"><button className="soft-button" type="button" disabled={busy} onClick={onClose}>Cancel</button><button className={`primary ${danger ? "danger-button" : ""}`} type="button" disabled={busy} onClick={onConfirm}>{busy ? "Removing..." : confirmLabel}</button></div></section></div>;
}
function MissedSessionPopup({ sessions, user, onClose }) {
  const first = sessions[0] || {};
  const learnerMissed = first.learner_id === user.id;
  return <div className="modal-backdrop missed-session-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}><section className="modal-card missed-session-card"><button className="icon-button modal-close" onClick={onClose} aria-label="Close missed session notice"><X size={18} /></button><span className="missed-session-icon"><AlertTriangle size={25} /></span><h2>{sessions.length > 1 ? "Missed sessions marked" : "Session missed"}</h2><p>{learnerMissed ? "A session passed without completion, so one time credit was deducted from your account. The mentor did not receive a credit." : "A session passed without completion. It was marked missed, and no teaching credit was added."}</p><div className="missed-session-list">{sessions.slice(0, 3).map((item) => <div key={item.id}><b>{item.skill_name || "Skill session"}</b><small>{formatSessionTime(item.scheduled_for)} • with {item.learner_id === user.id ? item.mentor_name : item.learner_name}</small></div>)}</div>{sessions.length > 3 && <small className="missed-session-extra">+{sessions.length - 3} more missed session{sessions.length - 3 > 1 ? "s" : ""}</small>}<button className="primary full" onClick={onClose}>Okay, got it</button></section></div>;
}
function SwipeHint({ direction }) {
  if (!direction) return null;
  const Icon = direction === "back" ? ArrowLeft : ArrowRight;
  return <div className={`swipe-hint ${direction}`}><span><Icon size={28} /></span></div>;
}

export default function App() {
  const loadingData = useRef(false);
  const missedNotices = useRef(createMissedSessionNotices(localStorage, sessionStorage));
  const missedNoticeUser = useRef(null);
  const promptedQuizzes = useRef(new Set());
  const [entrySession, setEntrySession] = useState(null);
  const [user, setUser] = useState(undefined), [data, setData] = useState(null), [page, setPage] = useState(() => appPages.includes(localStorage.getItem("gt-page")) ? localStorage.getItem("gt-page") : "home"), [theme, setTheme] = useState(() => localStorage.getItem("gt-theme") || "light");
  const [collapsed, setCollapsed] = useState(false), [booking, setBooking] = useState(null), [skillModal, setSkillModal] = useState(null), [quizModal, setQuizModal] = useState(null), [reward, setReward] = useState(null), [toast, setToast] = useState(null), [authMode, setAuthMode] = useState(null), [selectedContactId, setSelectedContactId] = useState(null), [selectedProfile, setSelectedProfile] = useState(null), [selectedMentor, setSelectedMentor] = useState(null), [needsBirthdate, setNeedsBirthdate] = useState(false), [appError, setAppError] = useState("");
  const [skillToRemove, setSkillToRemove] = useState(null), [removingSkill, setRemovingSkill] = useState(false), [showCreditFloat, setShowCreditFloat] = useState(true), [logoutConfirm, setLogoutConfirm] = useState(false), [missedPopup, setMissedPopup] = useState(null);
  const [appRatingPrompt, setAppRatingPrompt] = useState(null), [appRatingScore, setAppRatingScore] = useState(5), [appRatingFeedback, setAppRatingFeedback] = useState(""), [appRatingBusy, setAppRatingBusy] = useState(false);
  const [entryChoice, setEntryChoice] = useState(() => localStorage.getItem("gt-entry-choice") || (currentAuthToken() || localStorage.getItem("gt-page") || localStorage.getItem("gt-theme") ? "user" : ""));
  const [viewedMentors, setViewedMentors] = useState([]);
  const [swipeHint, setSwipeHint] = useState(null);
  const pageHistory = useRef(readPageStack("gt-page-history")), forwardHistory = useRef(readPageStack("gt-forward-history")), publicHistory = useRef([]), publicForward = useRef([]), touchStart = useRef(null), trackpadSwipe = useRef({ x: 0, y: 0, last: 0 }), dailyQuestClaiming = useRef(false), legacyMessageMigration = useRef(new Set()), splashQuote = useRef(randomQuote(new Date().getMinutes()));
  const notify = (message, error = false) => { setToast({ message, error }); setTimeout(() => setToast(null), 3200); };
  const load = async () => {
    if (loadingData.current) return;
    loadingData.current = true;
    try {
      setAppError("");
      const result = await api("/api/app");
      setData((current) => (
        current && result.debugError && (!result.mySkills || !result.mySkills.length)
          ? { ...result, mySkills: current.mySkills || [] }
          : result
      ));
      setUser(result.user);
      setNeedsBirthdate(false);
    } catch (e) {
      if (e.status === 428 || e.body?.needs_birthdate) {
        setNeedsBirthdate(true);
        setData(null);
        setAppError("");
      } else {
        setData(null);
        setAppError(e.message || "Could not load your GotTime workspace.");
        notify(e.message || "Could not load your GotTime workspace.", true);
      }
    } finally { loadingData.current = false; }
  };
  useEffect(() => {
    if (!data?.user?.id) return;
    let cancelled = false;
    let timer;
    const poll = async () => {
      if (!document.hidden) await load();
      if (!cancelled) timer = setTimeout(poll, 12000);
    };
    timer = setTimeout(poll, 12000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [data?.user?.id]);
  useEffect(() => {
    if (quizModal || appRatingPrompt || reward || entrySession) return;
    const ready = nextLearnerQuiz(data, promptedQuizzes.current);
    if (!ready) return;
    const key = `${data.user.id}:${ready.id}`;
    promptedQuizzes.current.add(key);
    let active = true;
    let delivered = false;
    api(`/api/sessions/${ready.id}/quiz`).then((quiz) => {
      if (active && quiz.canSubmit) {
        delivered = true;
        setQuizModal({ ...quiz, session: ready, mode: "take" });
      }
    }).catch(() => promptedQuizzes.current.delete(key));
    return () => { active = false; if (!delivered) promptedQuizzes.current.delete(key); };
  }, [data, quizModal, appRatingPrompt, reward, entrySession]);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const authToken = params.get("authToken");
    const authError = params.get("authError");
    const authDetail = params.get("authDetail");
    if (authToken) {
      storeAuthToken(authToken);
      params.delete("authToken");
      params.delete("authSuccess");
      const nextQuery = params.toString();
      history.replaceState({}, "", `${location.pathname}${nextQuery ? `?${nextQuery}` : ""}`);
    }
    if (authError) {
      const messages = { google_cancelled: "Google sign-in cancelled.", google_state: "Google sign-in expired before GotTime could finish. Try once more.", google_not_configured: "Google sign-in needs setup.", google_network: "Google sign-in could not reach Google.", google_token: "Google did not return an access token.", google_email: "Google did not share an email address.", google_server: "GotTime could not save this Google account." };
      const message = `${messages[authError] || "Google sign-in could not be completed."}${authDetail ? ` ${authDetail}` : ""}`;
      sessionStorage.setItem("gt-auth-notice", message);
      notify(message, true);
      params.delete("authError");
      params.delete("authDetail");
      const nextQuery = params.toString();
      history.replaceState({}, "", `${location.pathname}${nextQuery ? `?${nextQuery}` : ""}`);
      setAuthMode("login");
    }
    api("/api/auth/me")
      .then((r) => { storeAuthToken(r.authToken); setUser(r.user); if (r.user?.needs_birthdate) setNeedsBirthdate(true); else if (r.user) load(); })
      .catch((err) => {
        if (currentAuthToken()) {
          setUser({ id: "restoring", username: "Restoring session", role: "learner", credits: 0 });
          setAppError(err.message || "Could not restore your signed-in session.");
        } else {
          setUser(null);
        }
      });
  }, []);
  useEffect(() => {
    setViewedMentors(user?.id ? readDailyMentorViews(user.id, data?.dailyQuest?.date) : []);
    dailyQuestClaiming.current = false;
  }, [user?.id, data?.dailyQuest?.date]);
  useEffect(() => {
    if (!data?.user?.id) return;
    const refreshOnReturn = () => { if (!document.hidden) load(); };
    const delay = questResetDelay(data.dailyQuest);
    const timer = delay === null ? null : setTimeout(refreshOnReturn, delay);
    window.addEventListener("focus", refreshOnReturn);
    document.addEventListener("visibilitychange", refreshOnReturn);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", refreshOnReturn);
      document.removeEventListener("visibilitychange", refreshOnReturn);
    };
  }, [data?.user?.id, data?.dailyQuest?.date]);
  useEffect(() => { if (user?.id && user.id !== "restoring") ensureMessageKey(user.id).catch(() => {}); }, [user?.id]);
  useEffect(() => {
    const userId = data?.user?.id ?? null;
    if (missedNoticeUser.current !== userId) {
      setMissedPopup(null);
      missedNoticeUser.current = userId;
    }
    if (!userId) return;
    const unseen = missedNotices.current.takeUnseen(data.sessions, userId);
    if (unseen.length) setMissedPopup((previous) => [...(previous || []), ...unseen]);
  }, [data?.user?.id, data?.sessions]);
  // Legacy encryption runs only for the opened chat, so unopened messages stay unread.
  useEffect(() => { dailyQuestClaiming.current = Boolean(data?.dailyQuestClaimed); }, [data?.dailyQuestClaimed, data?.dailyQuest?.date]);
  useEffect(() => {
    document.documentElement.dataset.theme = user ? theme : "light";
    if (user) localStorage.setItem("gt-theme", theme);
  }, [theme, user]);
  useEffect(() => { if (appPages.includes(page)) localStorage.setItem("gt-page", page); }, [page]);
  const syncPageStacks = () => { writePageStack("gt-page-history", pageHistory.current); writePageStack("gt-forward-history", forwardHistory.current); };
  const goToPage = (next, { replace = false } = {}) => {
    if (!appPages.includes(next)) return;
    if (next === page) {
      if (next === "profile") setSelectedProfile(null);
      return;
    }
    if (next === "profile") setSelectedProfile(null);
    if (replace) {
      forwardHistory.current = [];
      syncPageStacks();
      setPage(next);
      return;
    }
    pageHistory.current = [...pageHistory.current.slice(-12), page];
    forwardHistory.current = [];
    syncPageStacks();
    setPage(next);
  };
  const goBackPage = () => {
    const previous = pageHistory.current.pop();
    if (!previous) return;
    forwardHistory.current = [...forwardHistory.current.slice(-12), page];
    syncPageStacks();
    setPage(previous);
  };
  const goForwardPage = () => {
    const next = forwardHistory.current.pop();
    if (!next) return;
    pageHistory.current = [...pageHistory.current.slice(-12), page];
    syncPageStacks();
    setPage(next);
  };
  const publicScreen = () => authMode || "guest";
  const setPublicScreen = (screen) => setAuthMode(screen === "guest" ? null : screen);
  const chooseEntry = (choice) => {
    localStorage.setItem("gt-entry-choice", choice);
    setEntryChoice(choice);
    publicHistory.current = [...publicHistory.current.slice(-12), "guest"];
    publicForward.current = [];
    setAuthMode(choice === "admin" ? "admin" : "register");
  };
  const goToAuth = (mode) => {
    const current = publicScreen();
    if (current === mode) return;
    publicHistory.current = [...publicHistory.current.slice(-12), current];
    publicForward.current = [];
    setAuthMode(mode);
  };
  const goBackPublic = () => {
    const previous = publicHistory.current.pop();
    if (!previous) return;
    publicForward.current = [...publicForward.current.slice(-12), publicScreen()];
    setPublicScreen(previous);
  };
  const goForwardPublic = () => {
    const next = publicForward.current.pop();
    if (!next) return;
    publicHistory.current = [...publicHistory.current.slice(-12), publicScreen()];
    setPublicScreen(next);
  };
  const enterApp = (nextUser) => {
    const landingPage = nextUser?.role === "admin" ? "admin" : "home";
    localStorage.setItem("gt-page", landingPage);
    localStorage.removeItem("gt-page-history");
    localStorage.removeItem("gt-forward-history");
    pageHistory.current = [];
    forwardHistory.current = [];
    publicHistory.current = [];
    publicForward.current = [];
    setPage(landingPage);
    setUser(nextUser);
    setAuthMode(null);
    if (nextUser?.needs_birthdate) {
      setNeedsBirthdate(true);
      setData(null);
    } else {
      setNeedsBirthdate(false);
      load();
    }
  };
  const handleTouchStart = (e) => {
    if (e.touches.length !== 1 || e.target.closest("input, textarea, select")) return;
    const t = e.touches[0];
    touchStart.current = { x: t.clientX, y: t.clientY, at: Date.now(), active: false };
    setSwipeHint(null);
  };
  const handleTouchMove = (e) => {
    if (!touchStart.current || e.touches.length !== 1) return;
    const t = e.touches[0], dx = t.clientX - touchStart.current.x, dy = t.clientY - touchStart.current.y;
    touchStart.current.active = Math.abs(dx) > 28 && Math.abs(dx) > Math.abs(dy) * 1.4;
    if (touchStart.current.active) setSwipeHint(dx > 0 ? "back" : "forward");
    if (touchStart.current.active && e.cancelable) e.preventDefault();
  };
  const handleTouchEnd = (e) => {
    if (!touchStart.current || e.changedTouches.length !== 1) return;
    const start = touchStart.current, t = e.changedTouches[0], dx = t.clientX - start.x, dy = t.clientY - start.y, elapsed = Date.now() - start.at;
    touchStart.current = null;
    setTimeout(() => setSwipeHint(null), 160);
    if (!start.active || Math.abs(dy) > 70 || elapsed > 900 || Math.abs(dx) < 90) return;
    if (!user) dx > 0 ? goBackPublic() : goForwardPublic();
    else dx > 0 ? goBackPage() : goForwardPage();
  };
  const handleTrackpadWheel = (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.target.closest("input, textarea, select, .message-stream, .custom-select-menu")) return;
    const horizontal = Math.abs(e.deltaX), vertical = Math.abs(e.deltaY);
    if (horizontal < 12 || horizontal < vertical * 1.35) return;
    const now = Date.now();
    if (now - trackpadSwipe.current.last > 420) trackpadSwipe.current = { x: 0, y: 0, last: 0 };
    trackpadSwipe.current.x += e.deltaX;
    trackpadSwipe.current.y += e.deltaY;
    const swipeX = trackpadSwipe.current.x;
    if (Math.abs(swipeX) < 105) {
      setSwipeHint(swipeX < 0 ? "back" : "forward");
      return;
    }
    if (e.cancelable) e.preventDefault();
    trackpadSwipe.current = { x: 0, y: 0, last: now };
    setSwipeHint(swipeX < 0 ? "back" : "forward");
    setTimeout(() => setSwipeHint(null), 220);
    if (!user) swipeX < 0 ? goBackPublic() : goForwardPublic();
    else swipeX < 0 ? goBackPage() : goForwardPage();
  };
    const sessionAction = async (id, action, options = {}) => {
    const item = data?.sessions?.find((s) => s.id === id);
    if (action === "join" && guardianNotice(data.user, item?.mode) && !options.guardianConfirmed) {
      setEntrySession(item);
      return null;
    }
    try {
      const r = await api(`/api/sessions/${id}/${action}`, { method: "POST", body: JSON.stringify({ guardianConfirmed: options.guardianConfirmed === true }) });
      setEntrySession(null);
      if (r.needsRating && !options.suppressRating) {
        const sessionItem = data?.sessions?.find((item) => item.id === id);
        setAppRatingPrompt({ sessionId: id, mentorName: sessionItem?.mentor_name || "your mentor", reward: r.reward });
        setAppRatingScore(5);
        setAppRatingFeedback("");
      } else if (r.reward && !options.suppressReward) {
        setReward({ type: "session" });
      } else {
        notify(r.message);
      }
      await load();
      return r;
    } catch (e) {
      notify(e.message, true);
      return null;
    }
  };
  const logout = async () => { await api("/api/auth/logout", { method: "POST" }); clearAuthToken(); localStorage.removeItem("gt-page"); localStorage.removeItem("gt-page-history"); localStorage.removeItem("gt-forward-history"); setNeedsBirthdate(false); setUser(null); setData(null); setPage("home"); };
  const closeMissedPopup = () => {
    setMissedPopup(null);
  };
    const closeAppRating = () => {
    const shouldReward = appRatingPrompt?.reward;
    setAppRatingPrompt(null);
    setAppRatingFeedback("");
    if (shouldReward) setReward({ type: "session" });
  };
  const submitAppRating = async (event) => {
    event.preventDefault();
    if (!appRatingPrompt) return;
    setAppRatingBusy(true);
    try {
      await api(`/api/sessions/${appRatingPrompt.sessionId}/rating`, { method: "POST", body: JSON.stringify({ score: appRatingScore, feedback: appRatingFeedback }) });
      await load();
      closeAppRating();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setAppRatingBusy(false);
    }
  };
  const booked = async (form) => { try { const r = await api("/api/sessions", { method: "POST", body: JSON.stringify(form) }); setBooking(null); notify(r.guardianNotice || r.message); await load(); } catch (e) { notify(e.message, true); } };
  const openQuiz = async (sessionItem, mode) => {
    try {
      const quiz = await api(`/api/sessions/${sessionItem.id}/quiz`);
      setQuizModal({ ...quiz, session: sessionItem, mode });
    } catch (e) {
      notify(e.message, true);
    }
  };
  const saveQuiz = async (sessionId, questions) => {
    try {
      const result = await api(`/api/sessions/${sessionId}/quiz`, { method: "POST", body: JSON.stringify({ questions }) });
      setQuizModal(null);
      notify(result.message || "Quiz attached.");
      await load();
    } catch (e) {
      notify(e.message, true);
      throw e;
    }
  };
  const submitQuiz = async (sessionId, answers) => {
    try {
      const result = await api(`/api/sessions/${sessionId}/quiz/submit`, { method: "POST", body: JSON.stringify({ answers }) });
      setQuizModal(null);
      notify(result.message || "Quiz submitted.");
      await load();
    } catch (e) {
      notify(e.message, true);
      throw e;
    }
  };
  const buyCredits = async (packageId) => {
    try {
      const result = await api("/api/credits/purchase", { method: "POST", body: JSON.stringify({ packageId }) });
      await loadExternalScript("https://checkout.razorpay.com/v1/checkout.js");
      if (!window.Razorpay) throw new Error("Razorpay checkout did not load.");
      const feedback = createCheckoutFeedback(notify);
      const checkout = new window.Razorpay({
        key: result.keyId,
        amount: result.order.amount,
        currency: result.order.currency,
        name: "GotTime?",
        description: `${result.package.name} - ${result.package.credits} credits`,
        modal: feedback.modal,
        order_id: result.order.id,
        prefill: { name: data?.user?.username || "", email: data?.user?.email || "" },
        theme: { color: "#2f7a62" },
        ...razorpayPaymentOptions(),
        handler: async (response) => {
          feedback.paymentReceived();
          try {
            const verified = await api("/api/credits/verify", { method: "POST", body: JSON.stringify(response) });
            notify(verified.message || "Payment successful.");
            await load();
          } catch (error) {
            notify(`${error.message || "Payment confirmation is pending."} If money was debited, do not pay again; check your wallet shortly or contact support.`, true);
          }
        },
      });
      checkout.on("payment.failed", (response) => notify(response?.error?.description || "Payment failed.", true));
      checkout.open();
    } catch (e) {
      notify(e.message, true);
    }
  };
  const donateSupport = async (amount) => {
    try {
      const result = await api("/api/donations/create", { method: "POST", body: JSON.stringify({ amount }) });
      await loadExternalScript("https://checkout.razorpay.com/v1/checkout.js");
      if (!window.Razorpay) throw new Error("Razorpay checkout did not load.");
      const feedback = createCheckoutFeedback(notify);
      const checkout = new window.Razorpay({
        key: result.keyId,
        amount: result.order.amount,
        currency: result.order.currency,
        name: "GotTime?",
        description: `Public donation - \u20B9${result.amount}`,
        modal: feedback.modal,
        order_id: result.order.id,
        prefill: { name: data?.user?.username || "", email: data?.user?.email || "" },
        theme: { color: "#2f7a62" },
        ...razorpayPaymentOptions(),
        handler: async (response) => {
          feedback.paymentReceived();
          try {
            const verified = await api("/api/donations/verify", { method: "POST", body: JSON.stringify(response) });
            notify(verified.message || "Thank you for supporting GotTime.");
            await load();
          } catch (error) {
            notify(`${error.message || "Donation confirmation is pending."} If money was debited, do not pay again; check back shortly or contact support.`, true);
          }
        },
      });
      checkout.on("payment.failed", (response) => notify(response?.error?.description || "Donation failed.", true));
      checkout.open();
    } catch (e) {
      notify(e.message, true);
    }
  };
  const addedSkill = async (form) => {
    try {
      const r = await api("/api/skills", { method: "POST", body: JSON.stringify(form) });
      setSkillModal(null);
      await load();
      if (r.rewardBadge) {
        setTimeout(() => setReward({ type: "skillBadge", name: r.rewardBadge }), 650);
      } else {
        notify(r.message);
      }
    } catch (e) {
      notify(e.message, true);
    }
  };
  const askRemoveSkill = (skill) => setSkillToRemove(skill);
  const removeSkill = async () => {
    if (!skillToRemove) return;
    const id = skillToRemove.skill_id || skillToRemove.id;
    try {
      setRemovingSkill(true);
      const r = await api(`/api/skills/${id}`, { method: "DELETE" });
      setData((current) => current ? { ...current, mySkills: (current.mySkills || []).filter((skill) => {
        const targetName = String(skillToRemove.name || skillToRemove.skill_name || "").trim().toLowerCase();
        const skillName = String(skill.name || skill.skill_name || "").trim().toLowerCase();
        return targetName ? skillName !== targetName : (skill.skill_id || skill.id) !== id;
      }) } : current);
      setSkillToRemove(null);
      notify(r.message);
      await load();
    } catch (e) {
      notify(e.message, true);
    } finally {
      setRemovingSkill(false);
    }
  };
  const saveProfile = async (form) => { try { const r = await api("/api/profile", { method: "PATCH", body: JSON.stringify(form) }); notify(r.message); await load(); } catch (e) { notify(e.message, true); } };
  const uploadProfilePhoto = async (file) => {
    try {
      const formData = new FormData();
      formData.append("profile_photo", file);
      const token = currentAuthToken();
      const response = await fetch("/api/profile/photo", { method: "POST", credentials: "include", headers: { ...(token ? { "X-GotTime-Auth": token } : {}) }, body: formData });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || "Profile photo upload failed.");
      notify(body.message || "Profile photo updated.");
      await load();
    } catch (e) { notify(e.message, true); }
  };
  const saveBanner = async ({ style, file }) => {
    try {
      const formData = new FormData();
      if (style) formData.append("banner_style", style);
      if (file) formData.append("banner_image", file);
      const token = currentAuthToken();
      const response = await fetch("/api/profile/banner", { method: "POST", credentials: "include", headers: { ...(token ? { "X-GotTime-Auth": token } : {}) }, body: formData });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || "Banner update failed.");
      notify(body.message || "Banner updated.");
      if (body.user) {
        setUser(body.user);
        setSelectedMentor((current) => current?.id === body.user.id ? { ...current, banner_style: body.user.banner_style, banner_image: body.user.banner_image } : current);
      }
      await load();
    } catch (e) { notify(e.message, true); throw e; }
  };
  const readNotifications = async () => { try { await api("/api/notifications/read", { method: "POST" }); await load(); } catch (e) { notify(e.message, true); } };
  const claimDailyQuest = async () => {
    const quest = data?.dailyQuest;
    if (!questIsCurrent(quest)) return;
    const target = Number(quest.target || 1);
    const mentorIds = readDailyMentorViews(user.id, quest.date);
    const currentProgress = dailyQuestCount(data, mentorIds);
    if (dailyQuestClaiming.current || data?.dailyQuestClaimed || currentProgress < target) return;
    dailyQuestClaiming.current = true;
    try {
      const result = await api("/api/quests/daily-curiosity", { method: "POST", body: JSON.stringify({ quest_key: quest.key, quest_date: quest.date, mentor_ids: mentorIds }) });
      if (result.awarded) {
        setReward((current) => current || { type: "questXp", name: quest.title, xp: quest.xp, badges: result.unlockedBadges || [] });
        const badgeMessage = result.unlockedBadges?.length ? ` Badges unlocked: ${result.unlockedBadges.join(", ")}.` : "";
        notify(`Daily quest complete: +${quest.xp} XP!${badgeMessage}`);
      } else {
        notify(result.message || (result.awarded ? "+50 XP earned!" : "Daily quest already claimed."));
      }
      await load();
    } catch (e) {
      dailyQuestClaiming.current = false;
      notify(e.message, true);
      if (e.status === 409) await load();
    }
  };
  const trackMentorView = (mentorId) => {
    const quest = data?.dailyQuest;
    if (!user?.id || !mentorId || Number(mentorId) === Number(user.id) || !questIsCurrent(quest)) return;
    const current = readDailyMentorViews(user.id, quest.date);
    if (current.includes(Number(mentorId))) return;
    const next = [...new Set([...current, Number(mentorId)])];
    writeDailyMentorViews(user.id, next, quest.date);
    setViewedMentors(next);
  };
  const openChat = (id) => { setSelectedContactId(Number(id)); goToPage("messages"); };
  const openMentor = (mentor) => {
    if (!mentor) return;
    setSelectedMentor(mentor);
    trackMentorView(mentor.id);
    goToPage("mentor-detail");
  };
  const openProfile = (profile) => { goToPage("profile"); setSelectedProfile(profile?.id === data?.user?.id ? null : profile); };
  const openNotification = (item) => item.type === "message" && item.target_contact_id ? openChat(item.target_contact_id) : goToPage(item.type === "session" ? "sessions" : "notifications");
  useEffect(() => {
    if (user?.id && data?.dailyQuest && !data.dailyQuestClaimed) claimDailyQuest();
  }, [viewedMentors, data?.dailyQuest?.date, data?.dailyQuest?.key, data?.dailyQuestProgress, data?.dailyQuestClaimed, user?.id]);
  const content = useMemo(() => {
    if (!data) return null; const props = { data, setPage: goToPage };
    return { home: <Dashboard {...props} openBooking={() => goToPage("discover")} sessionAction={sessionAction} openChat={openChat} viewedMentors={viewedMentors} />, discover: <Discover {...props} openMentor={openMentor} />, "mentor-detail": <MentorDetailPage {...props} mentor={selectedMentor} openBooking={(mentor) => setBooking({ mentor })} openChat={openChat} onMentorViewed={trackMentorView} saveBanner={saveBanner} />, sessions: <SessionsPage {...props} action={sessionAction} openBooking={() => goToPage("discover")} openQuiz={openQuiz} openChat={openChat} />, calendar: <CalendarPage {...props} />, skills: <SkillsPage {...props} openSkill={() => setSkillModal({ mode: "add" })} editSkill={(skill) => setSkillModal({ mode: "edit", skill })} askRemoveSkill={askRemoveSkill} />, badges: <BadgeVault {...props} />, messages: <MessagesPage {...props} refreshData={load} selectedContactId={selectedContactId} sessionAction={sessionAction} showReward={setReward} />, notifications: <NotificationsPage {...props} onNotificationClick={openNotification} />, leaderboard: <Leaderboard {...props} onOpenProfile={openProfile} />, profile: <Profile {...props} saveProfile={saveProfile} uploadPhoto={uploadProfilePhoto} profileUser={selectedProfile} />, subscription: <SubscriptionPage {...props} buyCredits={buyCredits} />, donation: <DonationPage {...props} donateSupport={donateSupport} />, testimonials: <TestimonialsPage {...props} />, about: <AboutPage {...props} />, admin: <AdminPage {...props} notify={notify} /> }[page];
  }, [data, page, selectedContactId, selectedProfile, selectedMentor, viewedMentors]);
  if (user === undefined) return <div className="splash"><Logo /><LoadingQuote quote={splashQuote.current} /><div className="loading-dots"><i /><i /><i /></div></div>;
  if (!user && !entryChoice && !authMode) return <div className="public-shell" onWheel={handleTrackpadWheel} onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd}><SwipeHint direction={swipeHint} /><GuestLanding onAuth={goToAuth} /></div>;
  if (!user && (authMode || entryChoice === "admin")) return <div className="public-shell" onWheel={handleTrackpadWheel} onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd}><SwipeHint direction={swipeHint} /><Auth initialMode={authMode === "admin" ? "login" : authMode || "login"} adminOnly={authMode === "admin" || entryChoice === "admin"} onBack={goBackPublic} onAuth={enterApp} /></div>;
  if (!user) return <div className="public-shell" onWheel={handleTrackpadWheel} onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd}><SwipeHint direction={swipeHint} /><GuestLanding onAuth={goToAuth} /></div>;
  if (needsBirthdate || user?.needs_birthdate) return <BirthdateGate onLogout={logout} onComplete={(nextUser) => { setUser(nextUser); setNeedsBirthdate(false); load(); }} />;
  if (!data) return <div className="splash"><Logo /><LoadingQuote quote={appError || splashQuote.current} />{!appError && <div className="loading-dots"><i /><i /><i /></div>}{appError && <div className="splash-actions"><button className="primary" onClick={load}>Try again</button><button className="text-button" onClick={logout}>Sign out</button></div>}{toast && <Toast {...toast} />}</div>;
  return <div className={`app-shell ${collapsed ? "side-collapsed" : ""}`} onWheel={handleTrackpadWheel} onTouchStart={handleTouchStart} onTouchMove={handleTouchMove} onTouchEnd={handleTouchEnd}><SwipeHint direction={swipeHint} /><Sidebar page={page} setPage={goToPage} user={data.user} data={data} onLogout={() => setLogoutConfirm(true)} collapsed={collapsed} setCollapsed={setCollapsed} /><div className="app-main"><Header page={page} user={data.user} data={data} theme={theme} setTheme={setTheme} setPage={goToPage} onReadNotifications={readNotifications} onNotificationClick={openNotification} onLogout={logout} />{content}<SiteFooter onNavigate={goToPage} /></div><nav className="mobile-nav">{navItems.filter((item) => item.id !== "badges" && (!item.adminOnly || data.user.role === "admin")).slice(0, 6).map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? "active" : ""} onClick={() => goToPage(id)}><Icon size={19} /><span>{label === "My sessions" ? "Sessions": label}</span></button>)}</nav>{entrySession && <ConfirmDialog title="Parent or guardian present?" message={guardianNotice(data.user, entrySession.mode)} confirmLabel="Yes, start session" onConfirm={() => sessionAction(entrySession.id, "join", { guardianConfirmed: true })} onClose={() => setEntrySession(null)} />}{missedPopup && <MissedSessionPopup sessions={missedPopup} user={data.user} onClose={closeMissedPopup} />}{showCreditFloat && <div className="credit-float"><button type="button" className="credit-float-main" onClick={() => { setShowCreditFloat(false); goToPage("subscription"); }}><span><Coins size={20} /></span><b>Ran out of credits?</b><small>Buy more</small></button><button type="button" className="credit-float-close" onClick={() => setShowCreditFloat(false)} aria-label="Close buy credits prompt"><X size={13} /></button></div>}{booking && <BookingModal data={data} mentor={booking.mentor} onClose={() => setBooking(null)} onBooked={booked} />}{skillModal && <SkillModal onClose={() => setSkillModal(null)} onAdded={addedSkill} initialSkill={skillModal?.skill || null} />}{quizModal && <QuizModal quiz={quizModal} onClose={() => setQuizModal(null)} onSave={saveQuiz} onSubmit={submitQuiz} />}{skillToRemove && <ConfirmDialog title="Remove this skill?" message={`Are you sure you want to remove ${skillToRemove.name || skillToRemove.skill_name || "this skill"} from your skill deck?`} confirmLabel="Yes, remove it" danger busy={removingSkill} onConfirm={removeSkill} onClose={() => !removingSkill && setSkillToRemove(null)} />}{logoutConfirm && <ConfirmDialog title="Sign out?" message="Are you sure you want to log out of GotTime?" confirmLabel="Yes, log out" onConfirm={logout} onClose={() => setLogoutConfirm(false)} />}{appRatingPrompt && <RatingDialog mentorName={appRatingPrompt.mentorName} score={appRatingScore} setScore={setAppRatingScore} feedback={appRatingFeedback} setFeedback={setAppRatingFeedback} busy={appRatingBusy} onSubmit={submitAppRating} onSkip={closeAppRating} />}{reward && <Reward reward={reward} onClose={() => setReward(null)} />}{toast && <Toast {...toast} />}</div>;
}












































