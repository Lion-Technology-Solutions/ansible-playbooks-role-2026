"""Dashboard presentation and localized, factual financial helpers."""
import calendar
from datetime import date
from decimal import Decimal,InvalidOperation,ROUND_DOWN

CURRENCIES=('EUR','USD','CAD','GBP','NGN','GHS','XAF')
TIMEZONES=('America/Toronto','Europe/Madrid','Europe/London','Africa/Lagos','Africa/Douala','America/New_York','UTC')
ES={
 'Dashboard':'Panel','My Programs':'Mis programas','Live Classes':'Clases en vivo','Assignments':'Tareas','Payments':'Pagos','Certificates':'Certificados','Support':'Soporte',
 'Students':'Estudiantes','Enrollments':'Inscripciones','Programs':'Programas','Classes':'Clases','Materials':'Materiales','Announcements':'Anuncios','Reports':'Informes','Settings':'Configuración',
 'Sign out':'Cerrar sesión','Welcome back':'Bienvenido de nuevo','Your learning. Your schedule. Your next step.':'Tu aprendizaje. Tu horario. Tu próximo paso.',
 'Next live class':'Próxima clase en vivo','Join Class':'Entrar a clase','View schedule':'Ver horario','Payment plan':'Plan de pago','View & Pay':'Ver y pagar',
 'Need help?':'¿Necesitas ayuda?','Chat with our support team.':'Habla con nuestro equipo de soporte.','Open Chat':'Abrir chat','Explore more programs':'Explora más programas',
 'Active':'Activo','Payment pending':'Pago pendiente','Explore Program':'Explorar programa','Curriculum':'Plan de estudios','Assignment due':'Próxima tarea','View Assignment':'Ver tarea',
 'Resume Learning':'Continuar aprendiendo','Progress':'Progreso','Completed':'Completado','In progress':'En curso','Next':'Siguiente','No upcoming class posted yet.':'Todavía no hay próxima clase publicada.',
 'Choose your program':'Elige tu programa','Create your account':'Crea tu cuenta','Complete enrollment':'Completa la inscripción','Continue':'Continuar','Account':'Cuenta','Payment':'Pago',
 'Account ready · Payment pending':'Cuenta lista · Pago pendiente','Account creation is automatic.':'La cuenta se activa automáticamente.','Full class access opens after verified payment.':'El acceso a las clases se activa después de verificar el pago.',
 'Pay in full':'Pago completo','Installment plan':'Plan de cuotas','Currency':'Moneda','Tuition':'Matrícula','Payment methods':'Métodos de pago','Continue to payment':'Continuar al pago',
 'All programs':'Todos los programas','All countries':'Todos los países','All time':'Todo el tiempo','Student target':'Meta de estudiantes','Paid students':'Estudiantes con pago confirmado',
 'Awaiting payment':'Esperando pago','Enrollment conversion':'Conversión de inscripciones','Enrollment Pipeline':'Proceso de inscripción','Account created':'Cuenta creada','Payment verified':'Pago verificado','Active learner':'Estudiante activo',
 'Revenue overview':'Resumen de ingresos','Collected':'Cobrado','Outstanding':'Pendiente','Forecast':'Proyección','Student':'Estudiante','Program':'Programa','Cohort':'Grupo','Action':'Acción','Review':'Revisar','View':'Ver',
 'Upload Material':'Subir material','Schedule Class':'Programar clase','Create Announcement':'Crear anuncio','Live overview':'Resumen en vivo','Apply filters':'Aplicar filtros','Lessons & files':'Lecciones y archivos',
 'Full name':'Nombre completo','Email address':'Correo electrónico','Country':'País','Language':'Idioma','Timezone':'Zona horaria','Create my student account':'Crear mi cuenta de estudiante',
 'Payment confirmed':'Pago confirmado','Enrollment active':'Inscripción activa','No certificate issued yet.':'Todavía no se ha emitido ningún certificado.',
}


def translated(text,language='en'):
    return ES.get(text,text) if language=='es' else text


def money_value(value):
    try: amount=Decimal(value)
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Enter a valid monetary amount.')
    if not amount.is_finite() or amount<=0 or amount>Decimal('999999999.99') or amount.as_tuple().exponent < -2:
        raise ValueError('Enter a positive amount with at most two decimal places.')
    return amount.quantize(Decimal('.01'))


def installment_schedule(total,count,start):
    base=(total/Decimal(count)).quantize(Decimal('.01'),rounding=ROUND_DOWN)
    values=[]
    for index in range(count):
        month=start.month-1+index
        year=start.year+month//12;month=month%12+1
        due=date(year,month,min(start.day,calendar.monthrange(year,month)[1]))
        values.append({'due':due,'amount':base if index<count-1 else total-base*(count-1)})
    return values


def csv_safe(value):
    value=str(value or '')
    return "'"+value if value.startswith(('=','+','-','@','\t','\r')) else value
