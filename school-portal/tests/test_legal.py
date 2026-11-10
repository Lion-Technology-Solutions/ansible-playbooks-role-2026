from sqlalchemy import select,func
from app import db,Student,PolicyAcceptance
from legal_content import POLICY_VERSION,TERMS,PRIVACY
from test_portal import csrf,register


def test_policies_public_and_registration_links(app):
    client=app.test_client()
    for path,sections in [('/terms',TERMS),('/privacy',PRIVACY)]:
        page=client.get(path)
        assert page.status_code==200
        for heading,body in sections:
            assert heading.encode() in page.data
        assert b'liontechacademy@gmail.com' in page.data
    page=client.get('/register')
    assert b'name="policy_acknowledged" value="yes" required' in page.data
    assert b'href="/terms"' in page.data and b'href="/privacy"' in page.data


def test_missing_false_and_stale_acknowledgment_create_no_account(app):
    client=app.test_client()
    for extra in ({},{'policy_acknowledged':'no','policy_version':POLICY_VERSION},{'policy_acknowledged':'yes','policy_version':'old'}):
        page=client.post('/register',data={'csrf':csrf(client,'/register'),'name':'Example','email':'example@example.com','country':'Canada','phone':'+14375551234','kind':'new','password':'student-password','confirm_password':'student-password',**extra})
        assert b'Read and acknowledge the current' in page.data
        with app.app_context():
            assert db.session.scalar(select(func.count()).select_from(Student))==0
            assert db.session.scalar(select(func.count()).select_from(PolicyAcceptance))==0
    register(client)
    with app.app_context():
        record=db.session.scalar(select(PolicyAcceptance))
        assert record.version==POLICY_VERSION and record.accepted_at and record.method=='password'
        assert db.session.get(Student,record.student_id)
