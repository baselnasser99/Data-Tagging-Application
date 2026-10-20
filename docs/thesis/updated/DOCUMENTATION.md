# Data Tagging: technical documentation

This describes the code on `master`. For what changed since the graded thesis, see [COMPARISON.md](../COMPARISON.md). Section numbers refer to the thesis.

## 1. Overview

Data Tagging shares labelling work fairly across a team. An admin uploads items (images or audio), writes questions about them, and gives the project to one or more groups. The app turns every *item × question* into a task, gives each task to the person with the lightest workload, keeps the work balanced as people join and leave, and exports the answers as a labelled dataset with an agreement score.

A second, simpler mode, the **survey project**, is a questionnaire in which every group member answers every question; a question can carry an attached image, audio or video file.

## 2. Roles

| Role (code) | Shown as | Can do |
|---|---|---|
| `admin` | Admin | Everything: people, groups, managers, all projects, exports |
| `project-admin` | Manager | Only the projects they manage: items, questions, settings, groups, progress, exports. A manager can exist without a project. |
| `end-user` | Member | See and answer their own tasks; see their own progress |

People are added by the admin one at a time (with a role, optionally straight into a group) or from a CSV file; a temporary password is shown when none is given. Only an admin adds people; there is no self sign-up. The admin can edit a member's name, email and password, and remove a member (§7).

## 3. Architecture (6.1)

- **Flask** application factory (`application/__init__.py`) with these blueprints:

  | Blueprint | Handles |
  |---|---|
  | `auth` | Sign-in and sign-out (no public sign-up: the admin adds people) |
  | `admin` | Dashboard, people, CSV import, surveys, downloads, profile |
  | `groups` | Groups and their members |
  | `manager` | Managers and their pages |
  | `annotation_admin` | Projects: create, upload, hand out, progress, export |
  | `annotation` | Members' tasks and answers |

- **Shared modules:** `distribution.py` (the algorithm), `export.py` (CSV/JSON, agreement), `permissions.py` (role checks as route decorators), `feedback.py` (messages), `models.py` (tables), `config.py` (settings from `.env`).
- **Database:** SQLAlchemy 2. MySQL 8 in production, SQLite for development and tests. `db.create_all()` creates the tables; on start, `_upgrade_schema()` adds columns and constraints introduced after the first release, so older databases keep working.
- **Frontend:** Jinja templates, Bootstrap 3 and AdminLTE 2, a custom design layer (`app.css`, `apple.css`), Chart.js for the answers-per-week chart. It honours reduced-motion, reduced-transparency and high-contrast settings.
- **No background workers.** Every operation runs inside the web request. The measurements in §9 show this is fast enough, so Celery and Redis would add two services without a measurable benefit.

## 4. Data model (6.2)

| Table | Holds |
|---|---|
| `User` | Name, email (unique), password hash, role, `active` (false once removed by an admin) |
| `UserGroup`, `UserGroupMembership` | Groups and who is in them |
| `Project` | Name, description, type (`is_annotation_project`), answers per task, max tasks per person, hand-out mode (`hand_out_per_item`) |
| `GroupProject` | Which groups work on which project |
| `ProjectAdmin` | Which manager runs which project, and who assigned them |
| `Question` | Text, answer type (free text or multiple choice, 2–6 options) |
| `ProjectImage` | An item: an image or an audio clip (`media_type`) |
| `AnnotationTask` | One item × one question; unique on (project, item, question) |
| `TaskAssignment` | One task given to one person; unique on (task, person) |
| `QuestionAnswer` | An answer, with person, time and assignment |
| `UserQuestionAssignment` | Survey questions given to a person |
| `ExportLog` | Every export: who, when, which format, how many answers |

## 5. Distribution algorithm (6.5.3)

**Terms.** A *task* is one item and one question. An *assignment* is a task given to one person. A person's *load* is their number of unfinished assignments in the project.

**Settings.** *Answers per task* (default 1) is how many different people answer each task. *Max tasks per person* is a cap. *Hand-out mode* is either each question on its own (default) or all of an item's questions to one person (useful for long audio clips).

```
distribute(project):
    create the tasks that are missing (never re-create or delete existing ones)
    release assignments above the cap or above "answers per task" (unfinished only)
    for each task with open slots, in a rotating question order:
        candidates ← members of the project's groups
                     who don't hold this task and are under the cap
        pick the candidate with the lowest load
            ties → fewest of this question → fewest tasks overall → lowest id
    rebalance(project)

rebalance(project):
    repeat:
        move one unstarted task from the busiest person to the least busy one
        if that narrows the gap, the receiver doesn't hold the task yet, and stays under the cap
    until no move narrows the gap
```

**Events.**
- **A member leaves a group:** their unfinished assignments in that group's projects are released (one `DELETE`) and handed out again with the same rule; their finished answers stay.
- **A member joins:** open slots are filled first, then `rebalance` runs.
- **Settings or questions change:** only the difference is applied. Lowering a setting releases unfinished work; finished answers are never removed.
- **Several groups on one project** share a single pool of tasks.
- **People who get no task**, for example because of the cap, are reported to the admin.

**Guarantees, each proven by a test:**
- Every task has as many different people as it needs.
- Nobody holds the same task twice.
- Loads differ by at most one task (one item's worth in per-item mode).
- Finished work is never moved or deleted.
- Results are deterministic, because ties end on the lowest id.

**Cost.** Choosing a person is a scan of the group's loads kept in memory, so most of the time goes to database writes. These are batched: one multi-row `INSERT` for new tasks and one for new assignments.

## 6. Export and agreement (6.5.7, 4.2)

- **CSV:** one row per answer: item, media type, question, annotator name and email, answer, time.
- **JSON:** nested by item, then question, then answers, with `exported_at` and the agreement per question.
- **Majority answer** per task with its share, for example "Red (2 of 2)". A tie has no majority.
- **Fleiss' kappa** per multiple-choice question, over tasks with at least two answers, with the Landis and Koch reading (slight, fair, moderate, substantial, almost perfect). It is checked against the published example from Fleiss (1971), κ = 0.210.
- **Free-text** answers of up to three words are compared after ignoring case and punctuation.
- Every export is written to `ExportLog`.

## 7. Removing and editing people

- **Edit:** name, email (must not be in use) and, optionally, a new password. Everything a person did is linked by their id, so nothing else changes. Files downloaded earlier keep the old name.
- **Remove:** takes the person out of every group, and their unfinished tasks are handed on (§5).
  - If they have finished work, they are switched off (`active = false`): they can't sign in, are signed out at once, and their answers stay in every export. **Restore** switches them back on.
  - If they have no finished work, they are deleted.

## 8. Security (6.5.1)

- Passwords are hashed (Werkzeug scrypt). There are no default accounts: the first admin comes from `.env`, and every secret lives there.
- Sessions use Flask-Login with HTTP-only, SameSite cookies and an idle timeout.
- After 5 wrong passwords in a row, an account pauses for 15 minutes, and even the right password is refused during the pause. The count lives on the user row, so it holds across Gunicorn workers and restarts.
- Every form carries a CSRF token (Flask-WTF). Pages are sent with `Cache-Control: no-store`.
- Every route checks the role from the database. Managers reach only their own projects, and only groups no other manager shares. Members reach only their own assignments.
- All user-written text is escaped, including text placed inside confirm dialogs.
- Uploaded files are served only to admins, the project's managers, and members of its groups. Everyone else gets 403, and survey attachments follow the same rule.
- The Content-Security-Policy allows scripts only from the app's own files (`script-src 'self'`): there are no inline scripts or event handlers, and Chart.js is served locally.
- An audit log records sign-ins, failed sign-ins and sign-outs. Errors get custom pages and go to a rotating log file.

## 9. Testing and evaluation (Ch. 7)

- **204 test functions, 252 test cases, 92% line coverage** (measured 20 Oct 2026 on Python 3.12), run by GitHub Actions on Python 3.11 and 3.12 for every change. Each distribution test runs in both hand-out modes.
- **Benchmark** (`scripts/benchmark.py`, run on MySQL 8.4 by `.github/workflows/benchmark.yml`, 20 Oct 2026). Every configuration ran 5 times with different seeds; each cell is the mean (min–max). r = answers per task; "2 groups" splits the 50 people into two groups of 25 on the same project.

  | Tasks | People | r | Groups | Hand out | 10% leave | 5% join |
  |---:|---:|---:|---:|---:|---:|---:|
  | 1,000 | 20 | 1 | 1 | 0.68 s (0.67–0.71) | 0.11 s (0.10–0.12) | 0.08 s (0.08–0.08) |
  | 10,000 | 50 | 1 | 1 | 7.66 s (7.36–8.45) | 2.06 s (2.00–2.17) | 1.59 s (1.49–1.73) |
  | 50,000 | 100 | 1 | 1 | 43.25 s (42.82–43.55) | 19.61 s (19.13–19.94) | 20.41 s (19.99–20.77) |
  | 10,000 | 50 | 2 | 1 | 11.44 s (11.39–11.52) | 2.48 s (2.44–2.51) | 2.39 s (2.33–2.45) |
  | 10,000 | 50 | 1 | 2 | 7.28 s (7.21–7.38) | 1.99 s (1.92–2.03) | 1.55 s (1.48–1.62) |

  | Tasks | People | r | Groups | Gap after hand-out | Open gap, half answered | Open gap after 10% leave | Open gap after 5% join | Every task owned | Finished work kept |
  |---:|---:|---:|---:|---:|---:|---:|---:|:-:|:-:|
  | 1,000 | 20 | 1 | 1 | 0 (0–0) | 13.4 (11–17) | 5.2 (4–8) | 1 (1–1) | 5/5 | 5/5 |
  | 10,000 | 50 | 1 | 1 | 0 (0–0) | 31.4 (27–34) | 5.6 (1–9) | 1 (1–1) | 5/5 | 5/5 |
  | 50,000 | 100 | 1 | 1 | 0 (0–0) | 56.2 (47–72) | 2.0 (1–6) | 1 (1–1) | 5/5 | 5/5 |
  | 10,000 | 50 | 2 | 1 | 0 (0–0) | 42.0 (38–45) | 2.0 (1–6) | 1 (1–1) | 5/5 | 5/5 |
  | 10,000 | 50 | 1 | 2 | 0 (0–0) | 31.4 (27–34) | 5.6 (1–9) | 1 (1–1) | 5/5 | 5/5 |

  The gap is the most tasks held by one person minus the fewest. It is 0 right after every hand-out. With half the work answered at random, the open (unfinished) gap is uneven by design; leavers' work goes to the least busy, which narrows it, and after newcomers join it is 1 task in every run. Every task kept its owners and no finished answer was lost in all 25 runs. Two groups on one project give the same results as one group of the same people, because the algorithm balances over everyone on the project. Standard deviations and every single run are on the workflow's summary page.
- **Load test:** 20 people signing in and answering at once: 240 requests, 0 errors, median 294 ms, 95th percentile 991 ms.

## 10. Deployment (6.4)

- **Docker Compose** with two services: `web` (Gunicorn) and `db` (MySQL 8), plus volumes for the database, uploads and logs.
- **Development:** `flask --app app run --debug` with SQLite.
- **Demo:** `scripts/seed_demo.py` fills an empty database with demo people, groups and projects.

## 11. Known limitations

- Every question is asked about every item, so a question about pictures also reaches audio clips. The workaround is one project per media type.
- Questions belong to projects, not to groups.
- A person has one role; a member who becomes a manager stops receiving tasks.
- There is no self-service password reset; the admin sets a new password.
- There are no email notifications.
- The interface is designed for desktop screens.
