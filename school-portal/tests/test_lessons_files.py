from test_portal import historical_payment
import hashlib
import io
from datetime import timedelta
from PIL import Image
from sqlalchemy import select

from app import db,Asset,AssetChunk,Lesson,LessonFile,SiteBranding,Enrollment,Course,seed,now
from file_storage import CHUNK_SIZE
from test_portal import admin,analytics,approve,csrf,post,register,request_enrollment


class MemoryStorage:
    def __init__(self): self.files={}
    def put(self,path,data): self.files[path]=data; return path
    def get(self,path): return self.files[path]


def setup(app):
    app.config['FILE_STORAGE']=MemoryStorage()
    manager,student=app.test_client(),app.test_client()
    admin(manager);register(student)
    course,cohort=analytics(app)
    return manager,student,course,cohort


def upload(manager,name,data,purpose='lesson'):
    token=csrf(manager,'/admin')
    r=manager.post('/admin/assets/start',headers={'X-CSRF-Token':token},data={'filename':name,'size':len(data),'purpose':purpose})
    assert r.status_code==200,r.data
    aid=r.json['id']
    for part in range(r.json['parts']):
        r=manager.post(f'/admin/assets/{aid}/chunks/{part}',headers={'X-CSRF-Token':token,'Content-Type':'application/octet-stream'},data=data[part*CHUNK_SIZE:(part+1)*CHUNK_SIZE])
        assert r.status_code==200,r.data
    r=manager.post(f'/admin/assets/{aid}/finish',headers={'X-CSRF-Token':token})
    assert r.status_code==200,r.data
    return aid


def png():
    output=io.BytesIO();Image.new('RGB',(8,8),'blue').save(output,format='PNG');return output.getvalue()


def create_lesson(manager,course,files,**extra):
    values=dict(action='create_lesson',course_id=course,title='Working with data',module='Module 1',body='Read the notes and practice.',position='1',publication_state='published',available_at='',file_ids=files)
    values.update(extra)
    return post(manager,'/admin',**values)


def test_lesson_documents_only_unlock_after_course_payment(app):
    manager,student,course,cohort=setup(app)
    aid=upload(manager,'Workbook.xlsx',b'PK\x03\x04Example spreadsheet bytes')
    create_lesson(manager,course,[aid])
    with app.app_context(): lesson=db.session.scalar(select(Lesson).where(Lesson.course_id==course));lid=lesson.id
    request_enrollment(student,course,cohort)
    assert student.get(f'/lessons/{lid}').status_code==403
    assert student.get(f'/files/{aid}').status_code==403
    assert app.test_client().get(f'/files/{aid}').location.endswith('/student/login')
    assert b'Working with data' not in student.get('/portal').data
    with app.app_context(): eid=db.session.scalar(select(Enrollment.id))
    historical_payment(student,eid,'FILE-ACCESS-PAID')
    post(manager,'/admin',action='verify_and_approve',enrollment_id=eid)
    assert b'Working with data' in student.get('/portal').data
    assert student.get(f'/lessons/{lid}').status_code==200
    r=student.get(f'/files/{aid}?download=1')
    assert r.status_code==200 and r.data.startswith(b'PK')
    assert 'attachment' in r.headers['Content-Disposition']
    assert 'vercel-storage.com' not in student.get(f'/lessons/{lid}').get_data(as_text=True)
    post(manager,'/admin',action='reject',enrollment_id=eid)
    assert student.get(f'/files/{aid}').status_code==403
    assert student.get('/portal').status_code==200


def test_chunk_retry_integrity_complete_ranges_and_head(app):
    manager,student,course,cohort=setup(app)
    token=csrf(manager,'/admin')
    data=b'A'*CHUNK_SIZE+b'BCDEFGH'
    r=manager.post('/admin/assets/start',data={'csrf':token,'filename':'clip.mp4','size':len(data),'purpose':'lesson'})
    aid=r.json['id']
    url=f'/admin/assets/{aid}/chunks/0'
    headers={'X-CSRF-Token':token,'Content-Type':'application/octet-stream'}
    assert manager.post(url,data=b'A'*CHUNK_SIZE,headers=headers).status_code==200
    assert manager.post(url,data=b'A'*CHUNK_SIZE,headers=headers).status_code==200
    assert manager.post(url,data=b'Z'*CHUNK_SIZE,headers=headers).status_code==409
    assert manager.post(f'/admin/assets/{aid}/finish',headers=headers).status_code==400
    assert manager.post(f'/admin/assets/{aid}/chunks/1',data=b'BCDEFGH',headers=headers).status_code==200
    assert manager.post(f'/admin/assets/{aid}/finish',headers=headers).status_code==200
    assert manager.post(f'/admin/assets/{aid}/finish',headers=headers).status_code==200
    assert manager.get(f'/files/{aid}').data==data
    r=manager.get(f'/files/{aid}',headers={'Range':f'bytes={CHUNK_SIZE-2}-{CHUNK_SIZE+3}'})
    assert r.status_code==206 and r.data==b'AABCDE'
    assert r.headers['Content-Range']==f'bytes {CHUNK_SIZE-2}-{CHUNK_SIZE+3}/{len(data)}'
    assert manager.get(f'/files/{aid}',headers={'Range':'bytes=-3'}).data==b'FGH'
    for value in ['bytes=99999999-','bytes=0-1,3-4','bytes=-0','nonsense']:
        assert manager.get(f'/files/{aid}',headers={'Range':value}).status_code==416
    r=manager.head(f'/files/{aid}')
    assert r.status_code==200 and not r.data and int(r.headers['Content-Length'])==len(data)
    with app.app_context(): assert len(list(db.session.scalars(select(AssetChunk))))==2


def test_upload_permissions_type_size_image_validation_and_csrf(app):
    manager,student,course,cohort=setup(app)
    assert student.post('/admin/assets/start',data={'csrf':csrf(student,'/portal'),'filename':'file.pdf','size':1,'purpose':'lesson'}).status_code==302
    assert manager.post('/admin/assets/start',data={'filename':'file.pdf','size':1,'purpose':'lesson'}).status_code==400
    token=csrf(manager,'/admin')
    for name,size,purpose in [('hack.html',20,'branding'),('logo.svg',20,'branding'),('file.pdf',0,'lesson'),('video.mp4',251*1024*1024,'lesson'),('file.pdf',1,'bad'),('file.pdf',1,'branding')]:
        assert manager.post('/admin/assets/start',data={'csrf':token,'filename':name,'size':size,'purpose':purpose}).status_code==400
    r=manager.post('/admin/assets/start',data={'csrf':token,'filename':'fake.png','size':5,'purpose':'branding'})
    aid=r.json['id']
    assert manager.post(f'/admin/assets/{aid}/chunks/0',data=b'bad',headers={'X-CSRF-Token':token}).status_code==400
    assert manager.post(f'/admin/assets/{aid}/chunks/0',data=b'WRONG',headers={'X-CSRF-Token':token}).status_code==200
    assert manager.post(f'/admin/assets/{aid}/finish',headers={'X-CSRF-Token':token}).status_code==400
    with app.app_context(): assert not db.session.get(Asset,aid).ready


def test_draft_scheduled_unpublish_archive_and_restore(app):
    manager,student,course,cohort=setup(app)
    approve(app,student,manager,course,cohort)
    aid=upload(manager,'Handout.pdf',b'%PDF-1.7\nSample')
    create_lesson(manager,course,[aid],publication_state='draft')
    with app.app_context(): lid=db.session.scalar(select(Lesson.id))
    assert student.get(f'/lessons/{lid}').status_code==404
    assert student.get(f'/files/{aid}').status_code==403
    assert manager.get(f'/admin/lessons/{lid}/preview').status_code==200
    create_lesson(manager,course,[aid],action='edit_lesson',lesson_id=lid,available_at=(now()+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'))
    assert student.get(f'/lessons/{lid}').status_code==404
    assert student.get(f'/files/{aid}').status_code==403
    create_lesson(manager,course,[aid],action='edit_lesson',lesson_id=lid)
    assert student.get(f'/files/{aid}').status_code==200
    token=csrf(manager,'/admin')
    manager.post(f'/admin/assets/{aid}/manage',data={'csrf':token,'action':'archive'})
    assert student.get(f'/files/{aid}').status_code==403
    assert b'Handout.pdf' not in student.get(f'/lessons/{lid}').data
    manager.post(f'/admin/assets/{aid}/manage',data={'csrf':token,'action':'restore'})
    assert student.get(f'/files/{aid}').status_code==200
    create_lesson(manager,course,[aid],action='edit_lesson',lesson_id=lid,publication_state='draft')
    assert student.get(f'/files/{aid}').status_code==403


def test_branding_only_selected_images_are_public_and_css_is_validated(app):
    manager,student,course,cohort=setup(app)
    data=png();logo=upload(manager,'Logo.png',data,'branding');private=upload(manager,'Diagram.png',data)
    assert app.test_client().get('/brand/logo').status_code==404
    values=dict(action='branding',site_name='LionTech Test Academy',tagline='Learn and grow',primary_color='#123456',accent_color='#aabbcc',logo_asset_id=logo,banner_asset_id=logo)
    post(manager,'/admin',**values)
    public=app.test_client()
    assert public.get('/brand/logo').data==data
    assert public.get('/brand/banner').data==data
    assert public.get(f'/files/{logo}').status_code==302
    assert student.get(f'/files/{logo}').status_code==403
    page=public.get('/')
    assert b'LionTech Test Academy' in page.data and b'/brand/banner' in page.data
    assert b'#123456' in public.get('/branding.css').data
    bad=post(manager,'/admin',**{**values,'primary_color':'red;/**'})
    assert b'valid six-digit hex colors' in bad.data
    bad=post(manager,'/admin',**{**values,'logo_asset_id':private})
    assert b'completed branding image' in bad.data
    token=csrf(manager,'/admin')
    result=manager.post(f'/admin/assets/{logo}/manage',data={'csrf':token,'action':'archive'},follow_redirects=True)
    assert b'Remove this image from branding' in result.data
    with app.app_context():
        with db.engine.begin() as connection:seed(connection)
        db.session.expire_all()
        assert db.session.get(SiteBranding,1).logo_asset_id==logo


def test_lesson_file_cannot_unlock_unpaid_other_course_or_branding_asset(app):
    manager,student,course,cohort=setup(app)
    approve(app,student,manager,course,cohort)
    with app.app_context(): second=db.session.scalar(select(Course).where(Course.slug=='devops')).id
    aid=upload(manager,'Private.pdf',b'%PDF-1.7\nPrivate')
    create_lesson(manager,second,[aid])
    with app.app_context():lid=db.session.scalar(select(Lesson.id).where(Lesson.course_id==second))
    assert student.get(f'/lessons/{lid}').status_code==403
    assert student.get(f'/files/{aid}').status_code==403
    logo=upload(manager,'Brand.png',png(),'branding')
    result=create_lesson(manager,course,[logo])
    assert b'completed, active lesson files' in result.data
    with app.app_context(): assert len(list(db.session.scalars(select(Lesson).where(Lesson.course_id==second))))==1


def test_file_library_rename_and_media_embeds(app):
    manager,student,course,cohort=setup(app)
    approve(app,student,manager,course,cohort)
    image=upload(manager,'Diagram.png',png())
    video=upload(manager,'Demo.webm',b'webm video sample')
    audio=upload(manager,'Lecture.mp3',b'mp3 audio sample')
    create_lesson(manager,course,[image,video,audio])
    with app.app_context():lid=db.session.scalar(select(Lesson.id).where(Lesson.course_id==course))
    page=student.get(f'/lessons/{lid}')
    assert b'<video' in page.data and b'<audio' in page.data and b'lesson-image' in page.data
    token=csrf(manager,'/admin')
    manager.post(f'/admin/assets/{audio}/manage',data={'csrf':token,'action':'rename','title':'Audio lecture 1'})
    assert b'Audio lecture 1' in manager.get('/admin/library').data
    assert student.get('/admin/library').status_code==302


def test_corrupt_png_checksum_returns_validation_error(app):
    import base64
    manager,student,course,cohort=setup(app)
    broken=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=')
    token=csrf(manager,'/admin')
    r=manager.post('/admin/assets/start',data={'csrf':token,'filename':'broken.png','size':len(broken),'purpose':'branding'})
    aid=r.json['id']
    manager.post(f'/admin/assets/{aid}/chunks/0',data=broken,headers={'X-CSRF-Token':token})
    r=manager.post(f'/admin/assets/{aid}/finish',headers={'X-CSRF-Token':token})
    assert r.status_code==400 and 'valid image' in r.json['error']
    with app.app_context():assert not db.session.get(Asset,aid).ready
