# Original vs. updated: claim by claim

**Original** is the thesis and documentation as graded, with the code at tag [`v1.0-thesis-submitted`](https://github.com/baselnasser99/Data-Tagging-Application/tree/v1.0-thesis-submitted). **Now** is the `master` branch and the revised thesis text.

## Claims the original code did not meet

| Topic | Thesis § | Original claim | Original code | Now |
|---|---|---|---|---|
| Fair distribution | 6.5.3 | Each task goes to the person with the lowest current load | Questions split in fixed shares (`total // members`); regenerating tasks after an edit deleted existing tasks, finished answers included | Least-loaded assignment, one task at a time, with tie-breaks and a per-person cap. New tasks are only ever added, never re-created, and finished answers are never deleted. See [`distribution.py`](../../application/distribution.py) |
| People leaving | 6.5.3 | Unfinished work is reassigned at once | Their pending assignments were deleted, not handed to anyone | Released and handed to the least-loaded remaining members; finished answers kept |
| People joining | 6.5.3 | Workloads stay balanced | No rebalancing | Open slots filled first, then unstarted work moves from the busiest to the least busy until no move narrows the gap |
| Data integrity | 4.x, 6.2 | Unique (project, item, question); no duplicate work | No such constraint | Unique (project, item, question) on tasks and unique (task, person) on assignments; older databases upgraded on start |
| Media types | 1, 6.5 | Text, image and audio labelling | Images only | Images and audio, as loose files or ZIPs; a built-in audio player |
| Export | 6.5.7 | Auditable JSON/CSV export | A plain text file per project and a CSV per person | CSV and JSON per project, with names, times, media type, the majority answer and **Fleiss' kappa**; every export logged |
| Testing | 7 | About 85% test coverage with pytest | No automated tests (one empty test file) | 230+ tests, ~92% line coverage, run by GitHub Actions on Python 3.11 and 3.12 |
| Security | 6.5.1 | Flask-Login, CSRF, secure cookies | Secret key and a default admin with password `123` written in the code; no CSRF protection | CSRF on every form, HTTP-only SameSite cookies with idle timeout, `Cache-Control: no-store`, audit log, no default accounts; see also the fixes below |
| Code structure | 6.1 | Blueprints for auth, admin, user, group, manager | One 1,850-line `admin.py` | Blueprints `auth`, `admin`, `groups`, `manager`, `annotation_admin`, `annotation`, plus shared `distribution`, `export`, `permissions` |
| Logging and errors | 6.5 | Error pages, rotating logs, audit log | No custom error pages or logs | Custom error pages, rotating application log, audit log of sign-ins |
| Configuration | 6.4 | MySQL 8, Python 3.11, settings in `.env` | Secret key and default admin in the code | Every setting and secret read from `.env`; MySQL 8 in Docker, SQLite for development and tests |

## Claims reworded rather than built

| Topic | Thesis § | Original claim | Decision |
|---|---|---|---|
| Background jobs | 5, 6.4 | Celery workers and a Redis queue; a 4-service Docker setup | **Not built, on purpose.** Measurements on MySQL 8 show the heaviest job (handing out 50,000 tasks) takes 43 s (mean of 5 runs) and a normal project of 1,000 tasks under 1 s. A queue would add two services for no measurable gain. Docker runs 2 services: web and MySQL. |
| Task leases | 4, 5 | Short-lived task leases with a time limit | Dropped by design: the app uses balanced assignment with lasting ownership, protected by database constraints, not self-selection with leases |
| Email notifications | 6.5 | Flask-Mail notifications | Future work |
| Autosave, backups, versioned edits, caching, minified assets, password reset | 5, 6 | Listed as features | Not features of the system; removed from the text or listed as future work |
| Frontend | 6.1 | Bootstrap 5 | Bootstrap 3 and AdminLTE 2, with a custom design layer (`app.css`, `apple.css`) |
| Original `DOCUMENTATION.md` | n/a | REST API under `/api/`, PostgreSQL, a task queue, offline mode, auto-scaling, data encryption | None of these existed; the [updated documentation](updated/DOCUMENTATION.md) describes only what the code does |

## Found and fixed after grading

These were not in the thesis, but a reviewer would find them:

- **Access control:** members could open and answer any survey, and download other people's answers, through two old pages (removed). Managers could see and assign projects that weren't theirs, and change members of groups shared with other managers (both now refused).
- **Script injection:** user-written text was printed unescaped in three pages, and names were placed inside JavaScript confirm dialogs. All text is now escaped, and names go through data attributes.
- **Data loss:** importing people into a group didn't give them work; editing questions reset project settings; a tie between answers was shown as a majority. All fixed, with tests.
- **Speed:** handing out work wrote one database row at a time. Batched writes made the hand-out 2.5× faster, and handling newcomers at 50,000 tasks 15× faster (250 s → 17 s).

## Added beyond the thesis

- **An evaluation with real numbers** (Ch. 7): speed and fairness at 1k, 10k and 50k tasks, with two answers per task and with two groups, 5 runs each on MySQL 8, and a load test with 20 people answering at once (0 errors, median 294 ms). Script: [`scripts/benchmark.py`](../../scripts/benchmark.py).
- **People management:** add people one at a time or from a CSV file, with search and filters; edit, remove and restore people (open work handed on, answers kept).
- **A redesigned interface:** a home page for each role, project status and progress, an answers-per-week chart, and a summary in the side panel.
- **Demo data:** [`scripts/seed_demo.py`](../../scripts/seed_demo.py) builds the demo shown in the screenshots.
