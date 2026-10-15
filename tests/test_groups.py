from application.models import UserGroup, UserGroupMembership


def test_create_group(admin_client, db):
    resp = admin_client.post(
        "/admin/create-group", data={"group_name": "Class A", "group_desc": "First class"}, follow_redirects=True
    )
    assert b"Group created successfully" in resp.data
    assert UserGroup.query.filter_by(name="Class A").one().description == "First class"


def test_group_names_are_unique(admin_client, db):
    db.session.add(UserGroup(name="Class A"))
    db.session.commit()
    resp = admin_client.post("/admin/create-group", data={"group_name": "Class A"}, follow_redirects=True)
    assert b"already exists" in resp.data
    assert UserGroup.query.count() == 1


def test_add_and_remove_member(admin_client, db, end_user):
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.commit()

    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": end_user.id})
    assert UserGroupMembership.query.filter_by(group_id=group.id, user_id=end_user.id).count() == 1

    resp = admin_client.post(
        f"/admin/manage-group/{group.id}", data={"action": "add_user", "user_id": end_user.id}, follow_redirects=True
    )
    assert b"already in this group" in resp.data

    admin_client.post(f"/admin/manage-group/{group.id}", data={"action": "remove_user", "user_id": end_user.id})
    assert UserGroupMembership.query.filter_by(group_id=group.id).count() == 0


def test_delete_group_removes_memberships(admin_client, db, end_user):
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.commit()
    db.session.add(UserGroupMembership(user_id=end_user.id, group_id=group.id))
    db.session.commit()

    admin_client.post(f"/admin/delete-group/{group.id}")
    assert UserGroup.query.count() == 0
    assert UserGroupMembership.query.count() == 0


def test_manage_group_page_lists_available_users(admin_client, db, end_user):
    group = UserGroup(name="G")
    db.session.add(group)
    db.session.commit()
    resp = admin_client.get(f"/admin/manage-group/{group.id}")
    assert resp.status_code == 200
    assert end_user.email.encode() in resp.data
