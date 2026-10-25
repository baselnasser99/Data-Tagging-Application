# Security policy

## Reporting a problem

Please don't open a public issue for a security problem. Use **Report a vulnerability** on this repository's **Security** tab instead, so the report stays private until it is fixed. Include the steps to reproduce it and what an attacker could do.

You can expect a reply within a week.

## Supported versions

Only the latest code on `master` receives fixes. The tag `v1.0-thesis-submitted` is the code as graded for the thesis and is kept unchanged on purpose: it still contains a fixed `SECRET_KEY` and a default admin password, so never deploy it.

A database first created by that tag still holds its default administrator (`admin@email.com`). When the current version starts on such a database and that account still has the published password, the password is replaced with a random one and the account's sessions end; set a new password with `python scripts/admin_manager.py`.

## What the app already does

Hashed passwords, CSRF tokens on every form, HTTP-only SameSite cookies with an idle timeout, a sign-in pause after repeated wrong passwords, role checks against the database on every page, escaped user text, security headers, and an audit log of sign-ins and exports. See the README's Security section for details.
