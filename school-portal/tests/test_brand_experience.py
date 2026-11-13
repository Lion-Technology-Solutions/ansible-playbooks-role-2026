"""Shared shell coverage, brand settings and private support boundaries."""
import re
from pathlib import Path
from html.parser import HTMLParser
import pytest
from sqlalchemy import select
from app import db,SiteBranding,Student
from test_portal import admin,register

class Page(HTMLParser):
    def __init__(self):super().__init__();self.support=0;self.brand=0;self.links=[];self.images=[];self.support_links=[];self.support_depth=0
    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes)
        if tag=='details':
            if attrs.get('id')=='site-support':self.support+=1;self.support_depth=1
            elif self.support_depth:self.support_depth+=1
        if 'brand-lockup' in attrs.get('class','').split():self.brand+=1
        if tag=='a':
            self.links.append(attrs.get('href',''))
            if self.support_depth:self.support_links.append(attrs.get('href',''))
        if tag=='img':self.images.append(attrs)
    def handle_endtag(self,tag):
        if tag=='details' and self.support_depth:self.support_depth-=1

@pytest.mark.parametrize('path',['/','/programs','/student/login','/register','/updates','/terms','/privacy','/staff/sign-in','/staff/recover','/admin/login','/support','/not-a-real-page'])
def test_public_and_error_pages_have_shared_brand_and_support(app,path):
    r=app.test_client().get(path);assert r.status_code in (200,404)
    page=Page();page.feed(r.data.decode())
    assert page.support==1 and page.brand>=1
    assert '/support' in page.links and 'mailto:liontechacademy@gmail.com' in page.links
    assert any(url.startswith('https://wa.me/') for url in page.links)
    assert b'brand-experience.css' in r.data and b'brand-experience.js' in r.data


def test_authenticated_student_and_admin_pages_have_support_without_identity_in_external_urls(app):
    learner=app.test_client();register(learner,email='private-person@example.test')
    manager=app.test_client();admin(manager)
    for client,paths in [(learner,['/portal','/enrollments','/account/location','/account/profile','/support']),(manager,['/admin','/admin?view=students','/admin?view=payments','/admin/team','/admin/integrations','/support'])]:
        for path in paths:
            r=client.get(path,follow_redirects=True);assert r.status_code==200,path
            page=Page();page.feed(r.data.decode());assert page.support==1,path
            for link in page.support_links:
                if link.startswith(('https://wa.me/','mailto:')):
                    assert 'private-person' not in link and 'LT-20' not in link


def test_staff_shell_uses_shared_logo_and_support(app):
    from test_staff_portal import setup,create,login
    _,sent=setup(app);client=login(app,sent,create(app))
    for path in ['/staff','/staff/profile','/staff/tasks','/staff/reports','/staff/notifications']:
        r=client.get(path,follow_redirects=True);assert r.status_code==200
        page=Page();page.feed(r.data.decode());assert page.support==1 and page.brand>=1


def test_saved_brand_colors_drive_global_tokens_after_component_css(app):
    app.test_client().get('/')
    with app.app_context():
        branding=db.session.get(SiteBranding,1);branding.primary_color='#123456';branding.accent_color='#ffffff';db.session.commit()
    client=app.test_client();css=client.get('/branding.css').data.decode()
    assert '--brand-primary:#123456' in css and '--brand-accent:#ffffff' in css and '--brand-on-accent:#000000' in css
    page=client.get('/').data.decode()
    assert page.index('brand-experience.css')<page.index('/branding.css')


def test_graphics_are_decorative_and_brand_assets_exist(app):
    page=Page();page.feed(app.test_client().get('/programs').data.decode())
    graphics=[image for image in page.images if '/program-' in image.get('src','')]
    assert len(graphics)==4
    for image in graphics:
        assert image['alt']=='' and image['width'] and image['height']
        assert app.test_client().get(image['src']).status_code==200


def test_all_page_templates_compile_and_full_documents_share_base(app):
    names=app.jinja_env.list_templates()
    for name in names:app.jinja_env.get_template(name)
    root=Path(app.root_path)/'templates'
    assert [p.name for p in root.glob('*.html') if '<html' in p.read_text()]==['base.html']
    for name in ['staff_base.html','login.html','legal.html','error.html','certificate.html']:
        assert "extends 'base.html'" in (root/name).read_text()


def test_public_help_does_not_clear_email_or_mfa_gates(app):
    from datetime import timedelta
    from app import AuthPolicy,AuthProfile,now
    learner=app.test_client();register(learner)
    with app.app_context():
        policy=db.session.get(AuthPolicy,1);policy.require_email=True;policy.require_mfa=False
        profile=db.session.scalar(select(AuthProfile));profile.email_verified_at=None;db.session.commit()
    assert learner.get('/support').status_code==200
    assert learner.get('/branding.css').mimetype=='text/css'
    assert learner.get('/portal').status_code==302
    with app.app_context():assert not db.session.scalar(select(AuthProfile)).email_verified_at
    # A fresh verified account whose MFA setup is due can read help, while learning stays gated.
    with app.app_context():
        policy=db.session.get(AuthPolicy,1);policy.require_email=False;policy.require_mfa=False;db.session.commit()
    other=app.test_client();register(other,email='mfa-person@example.test')
    with app.app_context():
        policy=db.session.get(AuthPolicy,1);policy.require_mfa=True
        person=db.session.scalar(select(Student).where(Student.email=='mfa-person@example.test'))
        profile=db.session.get(AuthProfile,person.id);profile.activated_at=now()-timedelta(days=8);db.session.commit()
    assert other.get('/support').status_code==200
    assert other.get('/branding.css').status_code==200
    assert other.get('/portal').status_code==302
