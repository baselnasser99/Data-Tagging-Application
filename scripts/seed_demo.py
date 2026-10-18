#!/usr/bin/env python3
"""Fill an EMPTY database with a realistic demo: people, groups, a manager,
two image projects with generated road-sign pictures, and a few weeks of answers.

Used for the README screenshots, and for anyone who wants to try the app.
Keep it away from real data by pointing it at its own database first:

    export DATABASE_URL=sqlite:///demo.db
    python scripts/seed_demo.py
    runapp      # or: flask --app app run --debug

Every demo account has the password printed at the end. The admin account is
the one from your .env (ADMIN_EMAIL / ADMIN_PASSWORD), as usual.
"""
import os
import random
import struct
import sys
import uuid
import zlib
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo root

from application import create_app, distribution  # noqa: E402
from application.admin import upload_dir  # noqa: E402
from application.extns import db  # noqa: E402
from application.models import (AnswerType, GroupProject, Project, ProjectAdmin, ProjectImage,  # noqa: E402
                                Question, QuestionAnswer, TaskAssignment, User, UserGroup, UserGroupMembership)

PASSWORD = "demo-password"
MEMBERS = {
    "Day shift": ["Amira Haddad", "Lucas Martin", "Priya Nair", "Tomás García", "Hannah Weber", "Kenji Sato"],
    "Night shift": ["Fatima Zahra", "Noah Kim", "Elena Rossi", "Omar Khalil", "Sofia Novak"],
    "Reviewers": [],  # a fresh group nobody has joined yet
}
SHAPES = ["Circle", "Triangle", "Square", "Octagon"]
COLOURS = {"Red": (204, 40, 40), "Yellow": (240, 190, 30), "Blue": (40, 90, 200), "Green": (30, 130, 80)}


def png(width, height, pixel):
    """A PNG file as bytes; pixel(x, y) -> (r, g, b). Standard library only."""
    rows = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def inside(shape, x, y, size):
    """Is (x, y) inside the shape, centred in a size x size picture?"""
    c, r = size / 2, size * 0.36
    dx, dy = x - c, y - c
    if shape == "Circle":
        return dx * dx + dy * dy <= r * r
    if shape == "Square":
        return abs(dx) <= r * 0.85 and abs(dy) <= r * 0.85
    if shape == "Octagon":
        return abs(dx) <= r and abs(dy) <= r and abs(dx) + abs(dy) <= r * 1.42
    # Triangle pointing up
    top, bottom = c - r, c + r * 0.75
    return top <= y <= bottom and abs(dx) <= (y - top) / (bottom - top) * r * 1.05


def sign(shape, colour, size=200):
    ink, sky = COLOURS[colour], (232, 236, 240)
    return png(size, size, lambda x, y: ink if inside(shape, x, y, size) else sky)


def add_items(project, count, rng):
    """Generated sign pictures, saved like the Upload page saves them. Returns {item id: (shape, colour)}."""
    folder = upload_dir("images", str(project.id))
    truth = {}
    for n in range(count):
        shape, colour = rng.choice(SHAPES), rng.choice(list(COLOURS))
        name = f"sign_{n + 1:02d}.png"
        path = os.path.join(folder, f"{uuid.uuid4()}_{name}")
        with open(path, "wb") as f:
            f.write(sign(shape, colour))
        item = ProjectImage(project_id=project.id, filename=os.path.basename(path), original_filename=name,
                            file_path=path, media_type="image")
        db.session.add(item)
        db.session.flush()
        truth[item.id] = (shape, colour)
    return truth


def mcq(project, text, options):
    q = Question(question=text, project_id=project.id, answer_type=AnswerType.MCQ, number_of_options=len(options))
    for n, option in enumerate(options, start=1):
        setattr(q, f"option{n}", option)
    db.session.add(q)
    return q


def main(app=None):
    rng = random.Random(7)  # the same demo every time
    app = app or create_app()
    with app.app_context():
        if Project.query.first() or User.query.filter(User.role != "admin").first():
            sys.exit("This database already has projects or people. Point DATABASE_URL at a new database "
                     "(see the top of this file) so the demo never mixes with real data.")
        admin = User.query.filter_by(role="admin").first()
        if admin is None:
            sys.exit("No admin account. Set ADMIN_EMAIL and ADMIN_PASSWORD in .env first.")

        def person(name, role="end-user"):
            first, last = name.lower().replace("á", "a").split()
            u = User(full_name=name, email=f"{first}.{last}@example.com", role=role)
            u.set_password(PASSWORD)
            db.session.add(u)
            return u

        manager = person("Maya Chen", "project-admin")
        groups = {}
        for group_name, names in MEMBERS.items():
            group = UserGroup(name=group_name)
            db.session.add(group)
            db.session.flush()
            groups[group_name] = group
            for name in names:
                member = person(name)
                db.session.flush()
                db.session.add(UserGroupMembership(user_id=member.id, group_id=group.id))
        person("Daniel Brooks")  # signed up, not in a group yet

        signs = Project(name="Road sign shapes", desc="Label the shape and main colour of each road sign photo. "
                        "Two people answer every question, so disagreements show up in the agreement score.",
                        is_annotation_project=True, answers_per_task=2, max_questions_per_user=100, user_id=admin.id)
        night = Project(name="Night-time signs", desc="Signs photographed after dark. Items are uploaded; "
                        "it still needs a group.", is_annotation_project=True, answers_per_task=1,
                        max_questions_per_user=100, user_id=admin.id)
        db.session.add_all([signs, night])
        db.session.flush()
        shape_q = mcq(signs, "What shape is the sign?", SHAPES)
        mcq(signs, "What is its main colour?", list(COLOURS))
        mcq(night, "What shape is the sign?", SHAPES)
        db.session.add(ProjectAdmin(user_id=manager.id, project_id=signs.id, assigned_by=admin.id))
        for g in ("Day shift", "Night shift"):
            db.session.add(GroupProject(group_id=groups[g].id, project_id=signs.id))
        truth = add_items(signs, 24, rng)
        add_items(night, 8, rng)
        db.session.commit()

        distribution.distribute(signs)

        # About two thirds of the work is done, spread over the last five weeks; one answer in ten is wrong.
        now = datetime.now()
        assignments = TaskAssignment.query.join(TaskAssignment.task).filter_by(project_id=signs.id).all()
        for a in rng.sample(assignments, len(assignments) * 2 // 3):
            shape, colour = truth[a.task.image_id]
            right = shape if a.task.question_id == shape_q.id else colour
            options = SHAPES if a.task.question_id == shape_q.id else list(COLOURS)
            text = right if rng.random() > 0.1 else rng.choice([o for o in options if o != right])
            when = now - timedelta(days=rng.triangular(0, 35, 3), hours=rng.uniform(0, 8))
            db.session.add(QuestionAnswer(user_id=a.user_id, question_id=a.task.question_id, answer=text,
                                          task_assignment_id=a.id, submitted_date=when))
            a.is_completed, a.completed_date = True, when
            a.task.current_answers += 1
            a.task.is_completed = a.task.current_answers >= a.task.required_answers
        db.session.commit()

        assert TaskAssignment.query.count() == 24 * 2 * 2, "every task should have two people"
        print(f"Demo ready: {User.query.count() - 1} people, {UserGroup.query.count()} groups, "
              f"{Project.query.count()} projects, {QuestionAnswer.query.count()} answers.")
        print(f"Sign in as the admin from .env, or as manager maya.chen@example.com or member "
              f"amira.haddad@example.com, password: {PASSWORD}")


if __name__ == "__main__":
    main()
