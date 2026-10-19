# Defence presentation: Data Tagging

Slide outline, about 12 minutes. Screenshots are in [`docs/screenshots/`](../../screenshots/).

---

## 1. The problem

- Machine-learning models learn from labelled examples, and someone has to make the labels.
- In university labs, a team usually shares this work through spreadsheets. The result is duplicated work, missed items, uneven loads, and no record of who labelled what.
- When someone leaves halfway, their share is simply lost.

## 2. The idea

- A web app that **hands out labelling work fairly and keeps it fair** while the team changes.
- **Admin:** uploads items (images or audio), writes questions, picks the groups.
- **App:** turns every item × question into a task and gives each task to the person with the lightest load.
- **Members:** answer their own tasks.
- **Result:** a labelled dataset with an agreement score.

## 3. Demo

1. *Dashboard:* projects, people, answers per week, progress per group.
2. *Project page:* status, progress, upload, hand out, settings.
3. *A member's task:* the picture, the question, one click to answer.
4. *Remove a member:* their open tasks move to the others at once; their answers stay.
5. *Export:* CSV/JSON with the majority answer and Fleiss' kappa.

## 4. The algorithm (6.5.3)

- **Least-loaded first:** each task goes to the eligible person with the fewest open tasks. Ties go to whoever has had that question least, then the fewest tasks overall, then the lowest id.
- **Never twice:** answers to one task always come from different people.
- **A cap per person.**
- **Leave:** release their unfinished tasks and hand them out again; finished work is kept.
- **Join:** fill open slots, then move unstarted work from the busiest to the least busy until no move helps.
- **Adding never destroys:** new questions, items or groups only create the missing tasks.

## 5. Data quality (4.2, 6.5.7)

- Several people can answer each task ("answers per task").
- A majority answer per task; a tie gives no majority.
- **Fleiss' kappa** per multiple-choice question, read on the Landis and Koch scale, and checked against Fleiss's own published example (κ = 0.210).

## 6. Architecture (6.1, 6.2)

- Flask with six blueprints. The algorithm, the export and the permissions are separate modules.
- SQLAlchemy 2 on MySQL 8. Database constraints guarantee one task per (item, question) and one assignment per (task, person).
- Docker Compose: the web app and MySQL.

## 7. Security (6.5.1)

- Hashed passwords, no default accounts, secrets only in `.env`.
- CSRF on every form, secure cookies with an idle timeout, an audit log.
- Every page checks the role; managers see only their own projects; all user text is escaped.
- Uploaded files only for admins, the project's managers and its groups' members; everyone else gets 403.
- Scripts only from the app's own files (`script-src 'self'`): no inline scripts, Chart.js served locally.

## 8. Evaluation (Ch. 7)

| Tasks / people | Setup | Hand out | 10% leave | 5% join | Gap after join |
|---:|---|---:|---:|---:|---:|
| 1,000 / 20 | 1 group, r = 1 | 0.68 s (0.67–0.71) | 0.11 s (0.10–0.12) | 0.08 s (0.08–0.08) | 1 (1–1) |
| 10,000 / 50 | 1 group, r = 1 | 7.66 s (7.36–8.45) | 2.06 s (2.00–2.17) | 1.59 s (1.49–1.73) | 1 (1–1) |
| 50,000 / 100 | 1 group, r = 1 | 43.3 s (42.8–43.6) | 19.6 s (19.1–19.9) | 20.4 s (20.0–20.8) | 1 (1–1) |
| 10,000 / 50 | 1 group, r = 2 | 11.44 s (11.39–11.52) | 2.48 s (2.44–2.51) | 2.39 s (2.33–2.45) | 1 (1–1) |
| 10,000 / 50 | 2 groups, r = 1 | 7.28 s (7.21–7.38) | 1.99 s (1.92–2.03) | 1.55 s (1.48–1.62) | 1 (1–1) |

- Mean (min–max) of 5 runs each on MySQL 8.4. Gap 0 after every hand-out; in all 25 runs every task kept its owners and no finished answer was lost.
- **Load test:** 20 people at once, 240 requests, 0 errors, median 294 ms.
- 204 test functions (252 test cases) at 92% line coverage, run on every change.

## 9. Decisions and limits

- **No Celery or Redis:** the slowest job takes seconds, so a queue would add complexity without benefit. The measurements back this.
- **No task leases:** the app uses balanced assignment with lasting ownership, not self-selection.
- **Limits:**
  - every question is asked about every item;
  - one role per person;
  - no email notifications;
  - no self-service password reset.

## 10. Future work

- Questions that apply only to images or only to audio.
- A reviewer role for a second quality check.
- AI pre-labelling that people correct.
- An API for other tools.
