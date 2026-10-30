import { useEffect, useRef, useState } from "react";
import { CalendarDays, Check, ChevronLeft, ChevronRight, Flame } from "lucide-react";
import { monthCells, monthLabel, shiftMonth } from "./activityCalendar";
import { questDay } from "./dailyQuest";

function useProfileData(api, path, keepPrevious = false) {
  const [result, setResult] = useState(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const cache = useRef(new Map());
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setError("");
    if (keepPrevious && cache.current.has(path)) {
      setResult(cache.current.get(path)); setLoading(false);
      return () => { active = false; controller.abort(); };
    }
    setLoading(true);
    if (!keepPrevious) setResult(null);
    api(path, { signal: controller.signal }).then((response) => {
      if (active) { cache.current.set(path, response); setResult(response); }
    })
      .catch((failure) => { if (active) setError(failure.message || "Could not load profile activity."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [api, path, retry, keepPrevious]);
  return { result, loading, error, retry: () => setRetry((value) => value + 1) };
}

function RequestState({ loading, error, retry }) {
  return <>{loading && <p role="status">Loading…</p>}{error && <div role="alert"><p>{error}</p><button className="soft-button" onClick={retry}>Try again</button></div>}</>;
}

export function CompletedSessionsDialog({ person, api, Modal, onClose }) {
  const [page, setPage] = useState(1);
  const request = useProfileData(api, `/api/users/${person.id}/completed-sessions?page=${page}`);
  const { result } = request;
  return <Modal title="Completed sessions" subtitle={`Learning and teaching sessions for ${person.username}`} onClose={onClose}>
    <RequestState {...request} />
    {result && <><p>{result.total} completed {result.total === 1 ? "session" : "sessions"}</p>
      {!result.total && <p>No completed sessions yet.</p>}
      <div className="profile-session-list">{result.sessions.map((item) => <article className="profile-session-item" key={item.id}>
        <CalendarDays size={22} /><div><h3>{item.skill_name}</h3><p>{item.role}{item.counterpart_name ? ` · with ${item.counterpart_name}` : ""}</p>{item.session_date && <small>{item.session_date}</small>}</div><span className="profile-session-complete"><Check size={16} /> Completed</span>
      </article>)}</div>
      {(page > 1 || result.has_more) && <div className="mentor-review-pagination"><button className="soft-button" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page}</span><button className="soft-button" disabled={!result.has_more} onClick={() => setPage((value) => value + 1)}>Next</button></div>}
    </>}
  </Modal>;
}

export function StreakCalendarDialog({ person, api, Modal, onClose }) {
  const [month, setMonth] = useState(() => questDay().slice(0, 7));
  const request = useProfileData(api, `/api/users/${person.id}/activity?month=${month}`, true);
  const { result } = request;
  const activeDays = new Set(result?.month === month ? result.active_days : []);
  const today = result?.today || questDay();
  return <Modal title="Activity streak" subtitle={`Active days for ${person.username}`} onClose={onClose}>
    <div className="streak-calendar" aria-busy={request.loading}>
      <div className="streak-calendar-summary"><Flame size={25} /><strong>{result?.streak ?? person.streak ?? "–"} day streak</strong></div>
      <div className="streak-calendar-navigation"><button type="button" className="icon-button" aria-label="Previous month" disabled={month <= "2000-01"} onClick={() => setMonth((value) => shiftMonth(value, -1))}><ChevronLeft size={20} /></button><h3>{monthLabel(month)}</h3><button type="button" className="icon-button" aria-label="Next month" disabled={month >= today.slice(0, 7)} onClick={() => setMonth((value) => shiftMonth(value, 1))}><ChevronRight size={20} /></button></div>
      <div className="streak-calendar-grid" aria-label={`${monthLabel(month)} active days`}>
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => <b className="streak-weekday" key={day}>{day}</b>)}
        {monthCells(month).map((date, index) => date ? <span key={date} className={`streak-day ${activeDays.has(date) ? "active-day" : ""} ${date === today ? "today" : ""}`} aria-current={date === today ? "date" : undefined} aria-label={`${date}${activeDays.has(date) ? ", active" : request.loading || request.error ? ", activity unavailable" : ", no recorded activity"}${date === today ? ", today" : ""}`}><span>{Number(date.slice(-2))}</span>{activeDays.has(date) && <Check size={12} aria-hidden="true" />}</span> : <span className="streak-day empty" key={`empty-${index}`} aria-hidden="true" />)}
      </div>
      {request.loading && <span className="streak-calendar-loading" role="status">Loading activity…</span>}
      {request.error && <div className="streak-calendar-error" role="alert"><p>{request.error}</p><button className="soft-button" onClick={request.retry}>Try again</button></div>}
    </div>
  </Modal>;
}
