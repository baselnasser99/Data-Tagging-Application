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

## 8. Evaluation (Ch. 7)

| Tasks | Hand out | 10% leave | 5% join | Load gap after |
|---:|---:|---:|---:|---:|
| 1,000 | 0.39 s | 0.08 s | 0.06 s | 1 |
| 10,000 | 4.55 s | 2.26 s | 1.84 s | 1 |
| 50,000 | 24.9 s | 15.4 s | 16.9 s | 1 |

- Measured on MySQL 8. Every task kept an owner and no finished answer was lost.
- **Load test:** 20 people at once, 240 requests, 0 errors, median 284 ms.
- 200+ automated tests at about 90% coverage, run on every change.

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
