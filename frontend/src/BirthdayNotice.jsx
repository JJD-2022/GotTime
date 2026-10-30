import { useEffect, useState } from "react";
import { Cake } from "lucide-react";
import { birthdayNoticeSeen, rememberBirthdayNotice } from "./birthdayNotice.js";

export default function BirthdayNotice({ userId, day, blocked, api, Modal }) {
  const [notice, setNotice] = useState(null);
  useEffect(() => {
    let active = true;
    let timer;
    setNotice(null);
    const load = async () => {
      try {
        const result = await api("/api/birthdays/today");
        if (active && result.people?.length && !birthdayNoticeSeen(localStorage, userId, result.date)) {
          setNotice(result);
        }
      } catch {
        // A temporary network failure must not break app startup or lose today's greeting.
        if (active) timer = setTimeout(load, 30000);
      }
    };
    load();
    return () => { active = false; clearTimeout(timer); };
  }, [userId, day, api]);
  const close = () => {
    rememberBirthdayNotice(localStorage, userId, notice.date);
    setNotice(null);
  };
  if (!notice || blocked) return null;
  return <Modal title="Today's birthdays" subtitle="A little celebration for our GotTime community." onClose={close}>
    <div className="birthday-celebration-list">{notice.people.map((person) => <div className="birthday-celebration-person" key={person.id}><Cake size={24} /><div><strong>{person.username}</strong><p>Happy birthday!</p></div></div>)}</div>
    <button className="primary full" type="button" onClick={close}>Lovely! 🎉</button>
  </Modal>;
}
