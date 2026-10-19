# GotTime verification — 18 October 2026

## Confirmed issues fixed

- Wrong-password sign-in could replace the stored password. Login now verifies the existing password,
  rejects unknown/inactive accounts and keeps registration separate. Unverified Google-profile login
  and OAuth development fallback no longer authenticate users.
- Session confirmation and payment verification could award credits more than once under concurrent
  requests. Transactions and row locks now cover the status, balance, ledger and notification writes.
- Concurrent bookings could overlap or commit more credits than a learner owns. Participant locks,
  overlap checks and active booking credit checks apply to both booking and chat scheduling.
- Completed/cancelled/missed sessions could be reopened by unchecked transitions. State transitions
  now reject invalid previous states. Rescheduling clears old attendance markers.
- A learner who opened a meeting could later be penalized as absent. The automatic no-show rule now
  requires that the learner never recorded joining; the deduction is atomic and only happens once.
- Guardian confirmation was only a booking checkbox. Joining through the API now also requires explicit
  confirmation for online users under 13 and offline users under 16, including chat-created sessions.
  Missing birthdate is rejected. Age is derived from birthdate instead of a stale age field.
- Video windows opened even when join authorization failed. They now navigate only after successful
  authorization. Meeting URLs are validated; popup blocking and join failures produce visible messages.
  There is an explicit “I have left the meeting” action because external providers may sever window tracking.
- Attached quizzes unlocked but did not automatically appear. The learner now receives the quiz after
  mentor completion (`awaiting_confirmation` or `completed`), with manual “Take quiz” still available.
  Quiz edits stop once unlocked; duplicate submits serialize; blank answers are rejected; saved answers
  and score are returned. Correct options are hidden until submission.
- Seen messages could still be edited through the API. The update now checks read/deleted state atomically.
- Background legacy chat migration read unopened conversations. It now only happens for the open chat.
  Chat and app polling avoid overlapping requests and pause in hidden tabs.
- Repeated schema queries/DDL on session paths added load and could implicitly commit transactions.
  Session table creation is now startup maintenance, positive schema checks are cached briefly, and
  `manage.py init` installs indexes. A bounded Waitress runner is included for Windows/Linux.

## Verification

Real MySQL 8.0.46, disposable database, Flask test clients and concurrent worker threads:
16 regression scenarios passed, including a final rerun. Covered registration, birthdate gating, profile,
skill add/remove, notifications, authorization, booking/range/credit commitments, chat scheduling,
guardian age boundaries, completion and rollback, missed sessions, quizzes, ratings, seen-message edits,
duplicate payments and duplicate reminders.

Three Node tests cover the guardian thresholds, unsafe meeting URLs and learner quiz eligibility.
The React production build and Python compilation passed. These do not substitute for a real
two-person camera/microphone call or browser UI verification.

Local HTTP burst measurement through Waitress (8 threads), small synthetic dataset:

| Measure | Result |
| --- | --- |
| Simultaneous clients | 50 |
| Authenticated dashboard requests | 500 |
| Successes / errors | 500 / 0 |
| Elapsed | 19.47 seconds |
| Throughput | 25.69 requests/second |
| Median latency | 1,670.1 ms |
| 95th percentile latency | 3,092.2 ms |

This is a bounded localhost test, not a production capacity guarantee or 50 simultaneous video calls.
The fixture has small histories; larger histories, network latency and public hosting need a new test.

## Remaining limitations / deployment work

- Video itself is hosted by Meet/Zoom/Teams/Jitsi. GotTime records opening/closing or self-reported departure;
  it does not receive provider attendance, duration or guardian-verification events. Live calls were not made.
- Birthdate and guardian presence are self-reported. A confirmation is not proof that an adult is present.
- Meeting links are taken from chat history, not a provider meeting tied uniquely to a session. Multiple
  scheduled sessions with the same contact still require selecting/sharing the appropriate link.
- In-app reminders work while requests or the scheduled job run. No OS task scheduler entry was installed.
- `flask_mail.py` is a local logging stub; it shadows the installed Flask-Mail package. Email delivery and
  password-reset email are not operational with this stub. In-app notifications are separate and were tested.
- Real Google OAuth, Razorpay checkout/capture, bank settlement and email delivery require configured
  external services and were not exercised. Payment concurrency tests use synthetic valid signatures only.
- Chat media currently inserts filenames into the message; it is not a working media-upload pipeline.
- Discovery returns only 20 mentors and large chat/session histories are not paginated. Add pagination,
  a dedicated lightweight update endpoint and production-sized data tests before promising larger scale.
- Production hosting, TLS, shared uploads, database backups, rate limiting and provider configuration
  are still deployment tasks. This audit does not certify every legacy HTML/admin/upload path.

Waitress is supported on Windows and uses worker threads:
[Flask deployment documentation](https://flask.palletsprojects.com/en/stable/deploying/waitress/).
