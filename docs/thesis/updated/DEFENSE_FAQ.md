# Defence questions and answers

Short, honest answers about the revised thesis, which describes release v3.0. The thesis doesn't follow every later change on `master`: figures such as test counts and coverage are as of that release, and features added since (for example the percent agreement for short free text) are in the README.

## The algorithm

**How is the work kept fair?**
Each task goes to the eligible person with the fewest unfinished tasks. Ties go to whoever has had that question least, then the fewest tasks overall, then the lowest id. When someone joins, unstarted work moves from the busiest person to the least busy until no move narrows the gap. Tests check that loads stay within one task of each other, and the benchmark measured a gap of 0 after hand-out and 1 after people joined.

**What happens when someone leaves halfway?**
Their unfinished tasks are released and handed to the least busy remaining members straight away. Their finished answers stay in the dataset. If an admin removes a person who has answers, the account is switched off rather than deleted, so the answers are kept.

**Can two people get the same task, or one person get it twice?**
Different people answering the same task is deliberate, through "answers per task", for example two people for agreement. One person can never hold the same task twice: the database has a unique constraint on (task, person), and the algorithm checks it too.

**What if there aren't enough people?**
If "answers per task" is higher than the number of people, or everyone has hit the cap, the extra work stays unassigned and the admin is told who got nothing and why.

**Is it deterministic?**
Yes. Ties end on the lowest id, so the same situation always gives the same result, which makes it testable.

**Why not just shuffle and split?**
A fixed split breaks the moment someone joins or leaves, and re-splitting would move or destroy finished work. The incremental approach only touches unfinished work.

## Data quality

**How do you measure label quality?**
Fleiss' kappa for each multiple-choice question, over tasks with at least two answers, read on the Landis and Koch scale. Each task also gets a majority answer, and a tie has no majority. The kappa code is checked against Fleiss's published example (κ = 0.210).

**What if an item can't be answered, for example a blurry picture?**
The member presses *Can't tell* instead of guessing. It finishes the task, so the work stays fair and nothing is handed out again, but it is left out of the majority answer and Fleiss' kappa: a guess would add noise to the agreement score, and an "I don't know" is not a vote for any answer. These items are flagged in the export and listed on the results page, where a manager can see whether the item is unclear or the question needs a better choice.

**Why no agreement score for free text?**
Two good descriptions rarely match word for word. Short answers of up to three words are compared after ignoring case and punctuation; longer text needs a person to review it.

## Performance and design choices

**Why did you not use Celery and Redis, as first planned?**
I measured first. On MySQL 8.4 (mean of 5 runs), handing out 10,000 tasks takes 7.7 s and 50,000 take 43 s; a normal project of 1,000 tasks takes 0.7 s. A queue would add two services for no visible gain. Batching the database writes did make a difference: 2.5× faster hand-out, and 15× faster handling of newcomers at 50,000 tasks.

**How does it behave under load?**
20 people signed in and answered at the same time: 240 requests, 0 errors, median 294 ms, 95th percentile 991 ms.

**Why Flask and not Django?**
Flask is small, and each part (sign-in, admin, groups, managers, projects, members) is its own blueprint. For a focused tool with three roles, Django's extra features would be overhead.

**Why Bootstrap 3 and AdminLTE 2?**
That is what the project started with. I kept them and added a design layer on top (`app.css`, `apple.css`) instead of spending the time on a migration that changes nothing for users.

## Security

**How are accounts protected?**
Passwords are hashed and there are no default accounts; the first admin and all secrets come from `.env`. Sessions use secure cookies with an idle timeout, every form has a CSRF token, and sign-ins are written to an audit log.

**How is access controlled?**
Every route checks the role from the database. Managers can only reach their own projects, and can only change groups that no other manager also uses. Members can only open their own assignments; anything else returns 403 or 404.
Uploaded files, survey attachments included, are served only to admins, the project's managers and members of its groups; everyone else gets 403.
The Content-Security-Policy allows scripts only from the app's own files (`script-src 'self'`): no inline scripts, and Chart.js is served locally.

**Did you find security problems in your own code?**
Yes, while rebuilding it:
- two old member pages let anyone open any survey and download other people's answers;
- managers could reach other managers' projects;
- user text was printed unescaped in a few places.

All are fixed, and each has a test.

## Testing

**How do you know it works?**
206 test functions (255 test cases), 92% line coverage (as of 20 Oct 2026), run by GitHub Actions on Python 3.11 and 3.12 for every change. The distribution tests each prove one rule, and run in both hand-out modes. Hand-testing each change found real bugs that are now covered by tests.

## Looking back

**What would you do next?**
- Questions that apply only to images or only to audio.
- A reviewer role.
- AI pre-labelling that people correct.
- An API.

**What did you learn?**
- Design the data model and the rules first, then write tests that prove the rules.
- Measure before adding infrastructure.
- Small reviewed changes catch bugs early.
