"""Audio clips in annotation projects (thesis: text, audio and image annotation).

A ZIP upload may contain images, audio clips or both. Each file becomes one
item; annotators see a picture for images and an audio player for audio,
and exports record which kind each item is.
"""
import csv
import io
import json

from sqlalchemy import inspect, text

from application import _upgrade_schema
from application.extns import db as _db
from application.models import ProjectImage, TaskAssignment, media_type_for
from tests.conftest import login
from tests.test_annotation_flow import _create_project, _upload

MIXED = {
    "photos/street.jpg": b"jpg",
    "clips/birdsong.mp3": b"mp3",
    "clips/nested/engine.WAV": b"wav",
    "voice.ogg": b"ogg",
    "readme.txt": b"ignored",
    "__MACOSX/clips/._birdsong.mp3": b"junk",
}


def test_media_type_is_detected_from_the_file_name():
    assert media_type_for("cat.PNG") == "image"
    assert media_type_for("song.mp3") == "audio"
    assert media_type_for("take2.m4a") == "audio"
    assert media_type_for("lossless.flac") == "audio"
    assert media_type_for("notes.txt") is None
    assert media_type_for("video.mp4") is None


def test_zip_with_images_and_audio_stores_each_with_its_type(admin_client):
    project = _create_project(admin_client)
    resp = _upload(admin_client, project, MIXED)
    assert b"Successfully uploaded 1 image and 3 audio files" in resp.data

    items = {i.original_filename: i for i in ProjectImage.query.filter_by(project_id=project.id)}
    assert set(items) == {"street.jpg", "birdsong.mp3", "engine.WAV", "voice.ogg"}
    assert items["street.jpg"].media_type == "image" and not items["street.jpg"].is_audio
    assert all(items[n].is_audio for n in ("birdsong.mp3", "engine.WAV", "voice.ogg"))


def test_zip_without_any_usable_files_is_reported(admin_client):
    project = _create_project(admin_client)
    resp = _upload(admin_client, project, {"readme.txt": b"x", "movie.mp4": b"y"})
    assert b"no new images or audio files" in resp.data
    assert ProjectImage.query.filter_by(project_id=project.id).count() == 0


def test_audio_tasks_are_generated_and_shown_with_a_player(app, admin_client, db, make_user):
    from application.models import UserGroup, UserGroupMembership

    group = UserGroup(name="Listeners")
    db.session.add(group)
    db.session.commit()
    annotator = make_user()
    db.session.add(UserGroupMembership(user_id=annotator.id, group_id=group.id))
    db.session.commit()

    project = _create_project(admin_client)
    _upload(admin_client, project, {"birdsong.mp3": b"mp3"})
    admin_client.post(f"/admin/generate-annotation-tasks/{project.id}", data={"group_id": group.id})

    mine = TaskAssignment.query.filter_by(user_id=annotator.id).all()
    assert len(mine) == 2  # one audio clip x two questions
    clip = mine[0].task.image

    client = app.test_client()
    login(client, annotator.email)
    for url in (
        "/user/annotation-tasks",
        f"/user/annotation-task/{mine[0].id}",
        f"/user/annotate-image/{clip.id}/{project.id}",
    ):
        page = client.get(url)
        assert page.status_code == 200, url
        assert b"<audio" in page.data, url
        assert clip.filename.encode() in page.data, url
        assert b'<img src="' + b"/static/uploads/images/" not in page.data, url

    # Answer both questions, then export: the clip is marked as audio.
    data = {f"answer_{a.id}": "a bird" for a in mine}
    client.post(f"/user/annotate-image/{clip.id}/{project.id}", data=data)
    assert all(a.is_completed for a in mine)

    exported = json.loads(admin_client.get(f"/admin/project/{project.id}/export.json").data)
    assert exported["images"][0]["media_type"] == "audio"
    assert exported["images"][0]["file_name"] == "birdsong.mp3"

    rows = list(csv.DictReader(io.StringIO(admin_client.get(f"/admin/project/{project.id}/export.csv").data.decode())))
    assert rows and all(r["media_type"] == "audio" for r in rows)


def test_admin_pages_show_audio_items(admin_client):
    project = _create_project(admin_client)
    _upload(admin_client, project, {"a.png": b"x", "b.mp3": b"y"})

    # Before any tasks exist (this page used to crash with no tasks).
    progress = admin_client.get(f"/admin/view-annotation-project/{project.id}")
    assert progress.status_code == 200
    assert b"(1 audio)" in progress.data
    assert b"Images and audio" in progress.data
    assert b"media-audio-thumb" in progress.data

    generate = admin_client.get(f"/admin/generate-annotation-tasks/{project.id}")
    assert generate.status_code == 200
    assert b"2 items" in generate.data  # the image and the audio clip both count (no preview strip here any more)

    upload_page = admin_client.get(f"/admin/upload-images/{project.id}")
    assert b"MP3, WAV, OGG, M4A, FLAC" in upload_page.data


def test_older_database_gets_the_media_type_column(app, admin_client):
    """A database created before audio support has no media_type column.

    Starting the app must add it, and existing rows must count as images.
    """
    project = _create_project(admin_client)
    _upload(admin_client, project, {"old.png": b"x"})
    _db.session.commit()
    _db.session.close()

    with _db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE project_image DROP COLUMN media_type"))
    assert "media_type" not in {c["name"] for c in inspect(_db.engine).get_columns("project_image")}

    _upgrade_schema(app)
    _upgrade_schema(app)  # running it again changes nothing

    assert "media_type" in {c["name"] for c in inspect(_db.engine).get_columns("project_image")}
    with _db.engine.connect() as conn:
        assert conn.execute(text("SELECT media_type FROM project_image")).scalar_one() == "image"
