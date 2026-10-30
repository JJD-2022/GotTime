import { useEffect, useState } from "react";
import { Star } from "lucide-react";
import { formatRating } from "./profileRatings";

export function MentorReviewButtons({ mentor, onOpen }) {
  const count = Number(mentor.rating_count || 0);
  const open = (event) => { event.stopPropagation(); onOpen(mentor); };
  return <div className="mentor-review-controls">
    <button type="button" onClick={open} aria-label={`View ${mentor.username}'s ${count} ratings, average ${formatRating(mentor.rating, "unrated")}`}>
      <Star size={15} fill={count ? "currentColor" : "none"} />
      <span>{formatRating(mentor.rating, "New")} · {count} {count === 1 ? "rating" : "ratings"}</span>
    </button>
  </div>;
}

export default function MentorReviewsDialog({ mentor, api, Modal, onClose }) {
  const [page, setPage] = useState(1), [retry, setRetry] = useState(0);
  const [result, setResult] = useState(null), [loading, setLoading] = useState(true), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError(""); setResult(null);
    api(`/api/mentors/${mentor.id}/reviews?page=${page}`, { signal: controller.signal })
      .then((response) => { if (active) setResult(response); })
      .catch((failure) => { if (active) setError(failure.message || "Could not load session reviews."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [mentor.id, page, retry, api]);
  return <Modal title="Ratings & reviews" subtitle={`Session feedback for ${mentor.username}`} onClose={onClose}>
    {loading && <p role="status">Loading session reviews…</p>}
    {error && <div role="alert"><p>{error}</p><button className="soft-button" onClick={() => setRetry((value) => value + 1)}>Try again</button></div>}
    {result && <>
      <div className="mentor-review-summary"><Star size={26} /><strong>{formatRating(result.summary.rating)}{result.summary.rating_count > 0 ? " / 5" : ""}</strong><span>{result.summary.rating_count} ratings · {result.summary.review_count} written reviews</span></div>
      {!result.summary.rating_count && <p>No session ratings yet.</p>}
      <div className="mentor-review-list">{result.reviews.map((review) => <article className="mentor-session-review" key={review.id}>
        <header><div><h3>{review.skill_name}</h3>{review.session_date && <small>Session: {review.session_date}</small>}</div><span className="mentor-review-stars" role="img" aria-label={`${review.score} out of 5 stars`}>{Array.from({ length: 5 }, (_, index) => <Star key={index} size={16} fill={index < review.score ? "currentColor" : "none"} />)}<b>{review.score}/5</b></span></header>
        {review.feedback ? <p>{review.feedback}</p> : <p className="review-no-feedback">No written feedback.</p>}
      </article>)}</div>
      {(page > 1 || result.has_more) && <div className="mentor-review-pagination"><button className="soft-button" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page}</span><button className="soft-button" disabled={!result.has_more} onClick={() => setPage((value) => value + 1)}>Next</button></div>}
    </>}
  </Modal>;
}
