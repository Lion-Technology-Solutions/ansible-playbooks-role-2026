import json
from sqlalchemy import select
from app import db,PortalTranslation
from test_portal import csrf,admin,register
from test_security import setup
from test_integrations import configure,http_ok


def test_language_selector_everywhere_and_french_persists(app):
    client=app.test_client()
    for path in ('/','/register','/admin/login','/terms','/privacy'):
        page=client.get(path)
        assert b'data-language-form' in page.data and b'portal-language-catalog' in page.data
    response=client.post('/preferences',data={'csrf':csrf(client),'language':'fr','timezone':'America/Toronto','next':'/'},follow_redirects=True)
    assert b'<html lang="fr">' in response.data
    assert client.get('/i18n/catalog').json['fr']['Password']=='Mot de passe'
    assert client.get('/i18n/catalog').json['fr']['Cameroon']=='Cameroun'
    response=client.post('/preferences',data={'csrf':csrf(client),'language':'en','timezone':'America/Toronto','next':'/'},follow_redirects=True)
    assert b'<html lang="en">' in response.data


def test_translation_rejects_arbitrary_private_input_and_requires_csrf(app):
    client=app.test_client()
    assert client.post('/i18n/translate',json={'language':'fr','texts':['Password']}).status_code==400
    token=csrf(client)
    result=client.post('/i18n/translate',headers={'X-CSRF-Token':token},json={'language':'fr','texts':['Password','private-student@example.com','secret-injected-data']})
    assert result.status_code==200 and result.json['translations']=={'Password':'Mot de passe'}
    assert client.post('/i18n/translate',headers={'X-CSRF-Token':token},json={'language':'xx','texts':['Password']}).status_code==400


def test_authored_content_translates_and_caches_with_no_arbitrary_provider_output(app):
    manager,student=setup(app);app.config['INTEGRATION_HTTP']=http_ok
    configure(app,manager,'openai',{'OPENAI_API_KEY':'unit-key','OPENAI_MODEL':'gpt-5.4-mini'});configure(app,manager,'openai',action='test')
    calls=[]
    def translator(values,payload):
        texts=json.loads(payload['input']);calls.append(texts)
        return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'translations':[{'source':source,'target':'Texte traduit'} for source in texts]})}]}]}
    app.config['TRANSLATION_REQUEST']=translator
    client=app.test_client();token=csrf(client)
    from app import Course
    with app.app_context():course=db.session.scalar(select(Course));course.description='A new public program description.';db.session.commit()
    data={'language':'fr','texts':['A new public program description.']}
    for _ in range(2):
        response=client.post('/i18n/translate',headers={'X-CSRF-Token':token},json=data)
        assert response.status_code==200,response.json
        assert response.json['translations']['A new public program description.']=='Texte traduit'
    assert len(calls)==1
    with app.app_context():assert db.session.scalar(select(PortalTranslation))


def test_private_draft_content_is_not_translated_for_public_visitors(app):
    from app import Course,ProgramProfile,CoursePublication
    client=app.test_client();token=csrf(client)
    with app.app_context():
        course=Course(name='Private draft',slug='private-draft',description='Confidential unpublished curriculum.',skills='Private')
        course.profile=ProgramProfile(duration='',schedule='',curriculum='Private content',tuition={});course.publication=CoursePublication(published=False,payment_links={});db.session.add(course);db.session.commit()
    response=client.post('/i18n/translate',headers={'X-CSRF-Token':token},json={'language':'fr','texts':['Confidential unpublished curriculum.']})
    assert response.status_code==200 and response.json['translations']=={}
