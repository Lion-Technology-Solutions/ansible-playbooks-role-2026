"""Shared presentation and public help; no changes to enrollment or authorization."""
from flask import render_template

SUPPORT_EMAIL='liontechacademy@gmail.com'


def contrast_ink(background):
    """Choose the higher-contrast light/dark foreground for a validated hex color."""
    channels=[int(background[index:index+2],16)/255 for index in (1,3,5)]
    linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels]
    luminance=sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))
    return '#ffffff' if 1.05/(luminance+.05) >= (luminance+.05)/.05 else '#000000'


def theme_css(branding):
    primary,accent=branding.primary_color,branding.accent_color
    return f''':root{{--brand-primary:{primary};--brand-accent:{accent};--brand-on-primary:{contrast_ink(primary)};--brand-on-accent:{contrast_ink(accent)};}}
body.brand-experience{{--navy:var(--brand-primary);--yellow:var(--brand-accent);--portal-navy:var(--brand-primary);--portal-yellow:var(--brand-accent);--dashboard-navy:var(--brand-primary);--dashboard-yellow:var(--brand-accent);}}
'''


def install(app):
    @app.get('/support')
    def support_center():
        return render_template('support_center.html')
    @app.context_processor
    def brand_context():return dict(support_email=SUPPORT_EMAIL)
