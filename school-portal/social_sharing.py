"""Public, server-rendered preview metadata; never share request query strings."""
from urllib.parse import urlencode
from flask import request,url_for,redirect
from providers import ORIGIN

PUBLIC={'home','register','login','application','course_page','updates','update_page','terms_of_service','privacy_policy'}


def install(app):
    @app.context_processor
    def sharing_context():
        public=request.endpoint in PUBLIC
        title='LionTech Academy | Your next chapter starts here'
        description='Build practical technology skills through live online learning and projects. Create your student account and explore available training programs.'
        if request.endpoint=='register':title='Create your student account | LionTech Academy'
        elif request.endpoint=='login':title='Student sign in | LionTech Academy'
        # Fixed first-party origin, public path only. No auth tokens, tracking or student IDs.
        canonical=ORIGIN+request.path if public else None
        invite=ORIGIN+'/register'
        text='Your next chapter starts here. Create your LionTech Academy student account and explore practical technology programs.'
        return dict(social_public=public,social_title=title,social_description=description,
                    social_canonical=canonical,social_image=ORIGIN+'/static/brand/student-welcome-v1.jpg',
                    invite_url=invite,invite_text=text,
                    share_whatsapp='https://wa.me/?'+urlencode({'text':text+' '+invite}),
                    share_facebook='https://www.facebook.com/sharer/sharer.php?'+urlencode({'u':invite}),
                    share_linkedin='https://www.linkedin.com/sharing/share-offsite/?'+urlencode({'url':invite}))

    @app.get('/favicon.ico')
    def favicon():
        return redirect(url_for('static',filename='brand/favicon-v1.ico'),code=302)
