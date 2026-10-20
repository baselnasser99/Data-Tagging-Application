<img src="docs/preview/preview@2x.png" alt="Data Tagging: labelling work, shared fairly. A chart shows open tasks per person staying within one task of each other after one person leaves and another joins." width="100%">

<p>
  <a href="https://github.com/baselnasser99/Data-Tagging-Application/actions/workflows/tests.yml"><img src="https://github.com/baselnasser99/Data-Tagging-Application/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12-1C2733" alt="Python 3.11 and 3.12">
  <img src="https://img.shields.io/badge/flask-3-1C2733" alt="Flask 3">
  <img src="https://img.shields.io/badge/MySQL-8-1C2733" alt="MySQL 8">
  <img src="https://img.shields.io/badge/coverage-~92%25-3366CC" alt="About 92% line coverage">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-F5CF3A" alt="MIT license"></a>
</p>

**A web app that shares labelling work fairly across a team.** An admin uploads images or audio clips and writes the questions. The app splits the work into tasks and hands them out so everyone carries the same load, even while people join or leave mid-project. The answers come back as a CSV or JSON dataset, with an agreement score showing how consistently people labelled.

Built as my diploma thesis at the University of Pécs, then rebuilt to production standards: security fixes, a fairness algorithm verified with measurements, 250+ automated tests, and a redesigned interface. [See what changed since the graded version →](docs/thesis/COMPARISON.md)

**[What it does](#what-it-does)** · **[How it stays fair](#how-the-hand-out-stays-fair)** · **[Measurements](#measured-not-assumed)** · **[Run it](#run-it)** · **[Docs](#documentation)**

<table>
  <tr>
    <td align="center" width="25%"><h3>1 task</h3><sub>most minus fewest open tasks per person, after people join</sub></td>
    <td align="center" width="25%"><h3>0 lost</h3><sub>finished answers lost across 25 benchmark runs</sub></td>
    <td align="center" width="25%"><h3>250+</h3><sub>automated tests, ~92% line coverage</sub></td>
    <td align="center" width="25%"><h3>294 ms</h3><sub>median response, 20 people answering at once, 0 errors</sub></td>
  </tr>
</table>

![Admin dashboard: projects, people, answers per week and progress per group](docs/screenshots/admin-dashboard.png)

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/project-overview.png" alt="A project's overview: tasks done, progress and actions"><br><sub><b>Project overview:</b> progress, items, hand-out and settings</sub></td>
    <td width="50%"><img src="docs/screenshots/member-task.png" alt="A member answering a multiple-choice question about a picture"><br><sub><b>Member's task:</b> one item, one question</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/users.png" alt="Searchable list of people with groups, projects and answers"><br><sub><b>People:</b> search, groups, edit and remove</sub></td>
    <td><img src="docs/screenshots/all-projects.png" alt="Project cards with status and progress"><br><sub><b>Projects:</b> status and progress at a glance</sub></td>
  </tr>
</table>

<sub>Screenshots use the demo data from <code>scripts/seed_demo.py</code>.</sub>

## What it does

| | |
|---|---|
| **Projects** | Upload images (PNG, JPG, GIF, BMP, WEBP) and audio (MP3, WAV, OGG, M4A, FLAC) as files or ZIPs. Ask free-text or multiple-choice questions about every item. |
| **Fair hand-out** | Every *item × question* becomes a task. Each task goes to the person with the lightest open workload, never twice to the same person, up to a per-person cap. Optionally, several people can answer each task. |
| **People come and go** | When someone leaves a group, their unfinished tasks are handed to the rest; when someone joins, workloads are rebalanced. Finished answers are never lost. |
| **Three roles** | **Admin** (everything), **Manager** (runs their own projects), **Member** (answers their tasks). Each role gets its own home page. |
| **Progress** | Status and progress on every project card, answers per week, progress per group, and a side-panel summary for each role. |
| **Export** | CSV or JSON per project, with who answered what and when, the majority answer, and **Fleiss' kappa** for inter-annotator agreement. Every export is logged. |
| **People admin** | Add people one at a time or from a CSV file, search and filter them, edit details, and remove people safely: their open work is handed on and their answers are kept. |
| **Surveys** | A simpler mode in which every member answers every question. |

## How the hand-out stays fair

```mermaid
flowchart LR
    U["Admin uploads items<br>and writes questions"] --> T["Every item × question<br>becomes a task"]
    T --> H["Each task goes to the<br>lightest open load"]
    H --> L{"Team changes"}
    L -- "someone leaves" --> R["Their unfinished tasks<br>are handed out again"]
    L -- "someone joins" --> B["Unstarted tasks move from<br>the busiest to the least busy"]
    R --> H
    B --> H
```

```
for each task (item, question) still needing answers:
    candidates = members of the project's groups
                 who don't hold this task yet and are under the per-person cap
    give it to the candidate with the fewest open tasks
        ties → fewest of this question → fewest tasks overall → lowest id
```

- **Someone leaves:** their unfinished tasks are released and handed out again with the same rule; their finished work stays.
- **Someone joins:** open slots are filled first, then unstarted tasks move from the busiest person to the least busy until no move narrows the gap. In the measurements below, loads end within one task of each other.
- **Several groups on one project** share a single pool of tasks.

The code is in [`application/distribution.py`](application/distribution.py), and the rules are pinned down by tests in [`tests/test_distribution.py`](tests/test_distribution.py).

## Measured, not assumed

[`scripts/benchmark.py`](scripts/benchmark.py) builds made-up teams and projects on a **MySQL 8** server in GitHub Actions ([workflow](.github/workflows/benchmark.yml)), then times the real distribution code and checks its fairness. Each setup runs 5 times; cells show the mean (min–max).

| Tasks | People | Setup | Hand out | 10% of people leave | 5% join | Load gap after joining |
|---:|---:|---|---:|---:|---:|---:|
| 1,000 | 20 | 1 group | 0.68 s (0.67–0.71) | 0.11 s (0.10–0.12) | 0.08 s (0.08–0.08) | 1 |
| 10,000 | 50 | 1 group | 7.66 s (7.36–8.45) | 2.06 s (2.00–2.17) | 1.59 s (1.49–1.73) | 1 |
| 50,000 | 100 | 1 group | 43.3 s (42.8–43.6) | 19.6 s (19.1–19.9) | 20.4 s (20.0–20.8) | 1 |
| 10,000 | 50 | 2 answers per task | 11.44 s (11.39–11.52) | 2.48 s (2.44–2.51) | 2.39 s (2.33–2.45) | 1 |
| 10,000 | 50 | 2 groups of 25 | 7.28 s (7.21–7.38) | 1.99 s (1.92–2.03) | 1.55 s (1.48–1.62) | 1 |

- The load gap (most open tasks held by one person minus fewest) is **0** right after hand-out.
- In all 25 runs, every task kept its owners and no finished answer was lost.
- Batching the database writes made the hand-out **2.5× faster**, and handling 5% newcomers at 50k tasks **15× faster** (250 s → 17 s).
- **Load test:** 20 people answering at once made 240 requests with **0 errors**; median response time 294 ms.

## Run it

**Codespaces (no install):** on GitHub, click **Code → Codespaces → Create codespace**, then:

```bash
cp .env.example .env          # set SECRET_KEY, ADMIN_EMAIL and ADMIN_PASSWORD
pip install -r requirements.txt
flask --app app run --debug
```

<details>
<summary><b>On your own computer</b></summary>

<br>

The same steps, after `python -m venv .venv` and activating it (`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows). Open http://127.0.0.1:5000 and sign in with the admin email and password from `.env`.

</details>

<details>
<summary><b>With Docker (Flask + MySQL 8)</b></summary>

<br>

`cp .env.example .env`, edit the passwords, then `docker compose up --build`.

</details>

<details>
<summary><b>Try it with demo data</b></summary>

<br>

Fill a separate demo database with 13 people, 3 groups, a manager, two image projects and five weeks of answers:

```bash
export DATABASE_URL=sqlite:///demo.db
python scripts/seed_demo.py
flask --app app run --debug
```

The script prints the demo sign-ins. It refuses to run on a database that already has people or projects in it.

</details>

## Tests

```bash
pip install -r requirements-dev.txt
pytest --cov
```

The tests cover sign-in and access control for each role, CSRF, the distribution rules (joins, leaves, caps, several answers per task), uploads, CSV import, exports and agreement scores, database rules, and the security fixes. GitHub Actions runs them on every push.

<details>
<summary><b>Tracking down a leaked connection or file</b></summary>

<br>

A database connection or file left open makes the tests fail on purpose (see `pytest.ini`). The failure often shows up in whichever test was running when Python cleaned up, not the one that leaked. To see where the leaked object was created, run the tests with allocation tracing (much slower):

```bash
PYTHONTRACEMALLOC=25 pytest --cov
```

On Windows PowerShell, run `$env:PYTHONTRACEMALLOC = "25"` first, then `pytest --cov`.

</details>

## Security

- Passwords are hashed (Werkzeug scrypt); there are no default accounts, and secrets live only in `.env`.
- Every form has a CSRF token. Cookies are HTTP-only and SameSite, with an idle timeout. Pages are sent with `Cache-Control: no-store`.
- Access is checked on every route: managers see only their own projects, and members only their own tasks.
- All user-written text is escaped, including inside confirm dialogs.
- After 5 wrong passwords, an account pauses for 15 minutes (stored in the database, so it holds across server processes).
- Sign-ins, failed sign-ins and sign-outs are written to an audit log.

## Tech stack

| | |
|---|---|
| **Backend** | Python 3.11, Flask (app factory and blueprints), SQLAlchemy 2, Flask-Login, Flask-WTF (CSRF) |
| **Database** | MySQL 8 in production, SQLite for development and tests; older databases are upgraded automatically on start |
| **Frontend** | Jinja templates, Bootstrap 3 and AdminLTE 2, a custom design layer (`app.css`, `apple.css`) with reduced-motion and high-contrast fallbacks, Chart.js |
| **Quality** | pytest with ~92% line coverage on Python 3.11 and 3.12, GitHub Actions, Dependabot |
| **Deployment** | Docker Compose (Gunicorn and MySQL) |

<details>
<summary><b>Project layout</b></summary>

```
application/
├── auth.py              sign-in, sign-out, sessions
├── admin.py             dashboard, people, CSV import, surveys, downloads
├── groups.py            groups and their members
├── manager.py           managers and their pages
├── annotation_admin.py  projects: create, upload, hand out, progress, export
├── annotation.py        members' tasks and answers
├── distribution.py      the fair hand-out algorithm
├── export.py            CSV/JSON export and agreement scores
└── permissions.py, models.py, templates/, static/
```

</details>

## Documentation

- [Technical documentation](docs/thesis/updated/DOCUMENTATION.md): architecture, data model, algorithm, export, security, evaluation and limitations
- [Original vs. updated](docs/thesis/COMPARISON.md): what the graded thesis claimed, what the code did then, and what is true now
- [Defence presentation](docs/thesis/updated/DEFENSE_PRESENTATION.md) and [defence Q&A](docs/thesis/updated/DEFENSE_FAQ.md)
- The code as graded: tag [`v1.0-thesis-submitted`](https://github.com/baselnasser99/Data-Tagging-Application/tree/v1.0-thesis-submitted)
- Thesis (revised 2026, PDF): see the [latest release](https://github.com/baselnasser99/Data-Tagging-Application/releases/latest).

## License

[MIT](LICENSE)
