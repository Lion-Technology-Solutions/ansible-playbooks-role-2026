"""A report copy contains work-report fields only, never HR/payroll documents."""
import io
from html import escape
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
FIELDS=[('completed','Tasks completed today'),('outcomes','Results and outcomes'),('pending','Pending tasks'),('blockers','Blockers and support needed'),('priorities','Priorities for next workday')]


def render(payload):
    if 'StaffSans' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('StaffSans',str(Path(__file__).parent/'static/fonts/DejaVuSans.ttf')))
    out=io.BytesIO();styles=getSampleStyleSheet()
    for name in ('Normal','Title','Heading2'):styles[name].fontName='StaffSans'
    styles['Normal'].fontSize=10;styles['Normal'].leading=15;styles['Title'].fontSize=21;styles['Heading2'].fontSize=12
    def para(value,style='Normal'):return Paragraph(escape(str(value)).replace('\n','<br/>'),styles[style])
    parts=[para('LIONTECH ACADEMY','Heading2'),Spacer(1,14),para('Daily Activity Report','Title'),Spacer(1,16)]
    for label,key in [('Reference','reference'),('Staff','name'),('Staff ID','staff_number'),('Supervisor','supervisor'),('Report date','date'),('Version','version'),('Submitted at','submitted_at')]:parts.append(para(label+': '+str(payload.get(key,''))))
    parts.append(Spacer(1,18))
    for key,label in FIELDS:parts.extend([para(label,'Heading2'),para(payload.get(key,'') or 'None reported'),Spacer(1,12)])
    parts.append(para(('Draft preview. This copy has not been submitted or emailed.' if payload.get('version')=='DRAFT' else 'This immutable submitted version is retained in the staff portal.')+' Supporting documents remain available only through authenticated portal access.'))
    SimpleDocTemplate(out,leftMargin=48,rightMargin=48,topMargin=42,bottomMargin=42,title='LionTech Daily Activity Report',author='LionTech Academy').build(parts)
    return out.getvalue()
