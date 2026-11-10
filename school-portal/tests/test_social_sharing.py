import io
from html.parser import HTMLParser
from urllib.parse import urlsplit,parse_qs
from PIL import Image
from test_portal import admin


class Metadata(HTMLParser):
    def __init__(self,body):
        super().__init__();self.meta={};self.links=[];self.anchors=[];self.feed(body.decode())
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta':self.meta[attrs.get('property') or attrs.get('name')]=attrs.get('content')
        if tag=='link':self.links.append(attrs)
        if tag=='a':self.anchors.append(attrs)


def test_anonymous_crawlers_receive_server_rendered_preview_and_public_image(app):
    client=app.test_client()
    for path in ['/','/register','/student/login']:
        response=client.get(path,headers={'User-Agent':'facebookexternalhit/1.1'})
        assert response.status_code==200
        head=Metadata(response.data)
        assert head.meta['og:image']=='https://www.liontechacademy.com/static/brand/student-welcome-v1.jpg'
        assert head.meta['og:image:width']=='1200' and head.meta['og:image:height']=='630'
        assert head.meta['twitter:card']=='summary_large_image'
        assert head.meta.get('robots')!='noindex,nofollow'
    image=client.get('/static/brand/student-welcome-v1.jpg')
    assert image.status_code==200 and image.mimetype=='image/jpeg'
    assert Image.open(io.BytesIO(image.data)).size==(1200,630) and len(image.data)<300000
    assert 'public' in image.headers['Cache-Control']
    assert client.get('/static/brand/favicon-32-v1.png').status_code==200
    assert client.get('/favicon.ico',follow_redirects=True).status_code==200


def test_share_links_and_canonical_exclude_query_strings_and_untrusted_host(app):
    response=app.test_client().get('/register?token=private-test-token&email=student@example.com',base_url='https://untrusted.example')
    head=Metadata(response.data)
    assert head.meta['og:url']=='https://www.liontechacademy.com/register'
    assert 'private-test-token' not in str(head.meta) and 'untrusted.example' not in str(head.meta)
    assert b'https://www.liontechacademy.com/register' in response.data
    shares=[a['href'] for a in head.anchors if a.get('href','').startswith(('https://wa.me/?','https://www.facebook.com/sharer/','https://www.linkedin.com/sharing/'))]
    assert len(shares)==3
    for link in shares:assert 'private-test-token' not in link and 'untrusted.example' not in link
    whatsapp=next(link for link in shares if link.startswith('https://wa.me/'))
    assert parse_qs(urlsplit(whatsapp).query)['text'][0].endswith('https://www.liontechacademy.com/register')


def test_private_pages_are_not_shared_and_registration_requirements_remain(app):
    client=app.test_client();admin(client)
    head=Metadata(client.get('/admin').data)
    assert head.meta['robots']=='noindex,nofollow' and 'og:url' not in head.meta
    form=app.test_client().get('/register').data
    assert b'policy_acknowledged' in form and b'name="confirm_password"' in form
    assert b'signup-grid' in form and b'Turn your curiosity into practical skills.' in form
