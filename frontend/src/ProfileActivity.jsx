import { useEffect, useState } from "react";
import { CalendarDays, Check, ChevronLeft, ChevronRight, Flame } from "lucide-react";
import { monthCells, monthLabel, shiftMonth } from "./activityCalendar";

function useProfileData(api, path) {
  const [result, setResult] = useState(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setLoading(true); setError(""); setResult(null);
    api(path, { signal: controller.signal }).then((response) => { if (active) setResult(response); })
      .catch((failure) => { if (active) setError(failure.message || "Could not load profile activity."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [api, path, retry]);
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
  const [month, setMonth] = useState("");
  const request = useProfileData(api, `/api/users/${person.id}/activity${month ? `?month=${month}` : ""}`);
  const { result } = request;
  const activeDays = new Set(result?.active_days || []);
  return <Modal title="Activity streak" subtitle={`Active days for ${person.username}`} onClose={onClose}>
    <RequestState {...request} />
    {result && <div className="streak-calendar">
      <div className="streak-calendar-summary"><Flame size={25} /><strong>{result.streak} day streak</strong></div>
      <div className="streak-calendar-navigation"><button type="button" className="icon-button" aria-label="Previous month" disabled={result.month <= "2000-01"} onClick={() => setMonth(shiftMonth(result.month, -1))}><ChevronLeft size={20} /></button><h3>{monthLabel(result.month)}</h3><button type="button" className="icon-button" aria-label="Next month" disabled={result.month >= result.today.slice(0, 7)} onClick={() => setMonth(shiftMonth(result.month, 1))}><ChevronRight size={20} /></button></div>
      <div className="streak-calendar-grid" aria-label={`${monthLabel(result.month)} active days`}>
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day) => <b className="streak-weekday" key={day}>{day}</b>)}
        {monthCells(result.month).map((date, index) => date ? <span key={date} className={`streak-day ${activeDays.has(date) ? "active-day" : ""} ${date === result.today ? "today" : ""}`} aria-current={date === result.today ? "date" : undefined} aria-label={`${date}${activeDays.has(date) ? ", active" : ", no recorded activity"}${date === result.today ? ", today" : ""}`}><span>{Number(date.slice(-2))}</span>{activeDays.has(date) && <Check size={12} aria-hidden="true" />}</span> : <span key={`empty-${index}`} aria-hidden="true" />)}
      </div><p className="streak-calendar-legend"><span /> Highlighted days = opened GotTime while signed in.</p><small className="streak-history-note">{result.history_note}</small>
    </div>}
  </Modal>;
}
