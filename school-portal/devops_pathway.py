"""One six-month DevOps enrollment, three learning levels and a fixed payment plan."""
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select
from devops_curriculum import MODULES

MONTHLY = Decimal('399.00')
TOTAL = MONTHLY * 6
STAGES = [dict(key=key,slug='devops',name=name,weeks=8,offset=index*16,price=str(TOTAL),
    summary=summary,titles=[title for _,titles in MODULES[index*2:index*2+2] for title in titles])
    for index,(key,name,summary) in enumerate([
        ('foundation','Foundation','Start with Linux, Git, scripting, AWS and infrastructure automation.'),
        ('intermediate','Intermediate','Build delivery pipelines, containers, Kubernetes and GitOps workflows.'),
        ('advanced','Advanced','Operate secure production systems and deliver your portfolio capstone.')])]
PAYMENTS = [dict(label='Initial enrollment payment — credited toward month 1',amount='99.00',timing='Due immediately on enrollment'),
    dict(label='Balance of month 1',amount='300.00',timing='Seven days after your first class')] + [
    dict(label=f'Month {month} tuition',amount='399.00',timing='Due on the 1st; monthly payments begin after the first-month balance deadline') for month in range(2,7)]


def payment_dates(stages,start,enrolled_on):
    balance=start+timedelta(days=7) if start else None
    dates=[enrolled_on,balance]
    for offset in range(1,len(stages)-1):
        if not balance:dates.append(None);continue
        month=balance.month-1+offset
        dates.append(date(balance.year+month//12,month%12+1,1))
    return [dict(part,due=due.isoformat() if due else None,first_class=start.isoformat() if start else None) for part,due in zip(stages,dates)]


def seed_devops(connection,m):
    Course,Policy,Price,Profile=(m[k] for k in ('Course','CoursePaymentPolicy','GlobalTuition','ProgramProfile'))
    cid=connection.execute(select(Course.id).where(Course.slug=='devops')).scalar()
    if not cid or connection.execute(select(Policy.course_id).where(Policy.course_id==cid)).first():return
    connection.execute(Policy.__table__.insert().values(course_id=cid,currency='USD',total=TOTAL,checkout_url='',stages=PAYMENTS,enabled=True))
    values=dict(currency='USD',total=TOTAL,installments=len(PAYMENTS),full_url='',installment_url='')
    table=Price.__table__
    if connection.execute(select(Price.course_id).where(Price.course_id==cid)).first():
        connection.execute(table.update().where(Price.course_id==cid).values(**values))
    else:connection.execute(table.insert().values(course_id=cid,**values))
    connection.execute(Profile.__table__.update().where(Profile.course_id==cid).values(duration='24 weeks · 6 months',schedule='Saturday and Sunday. Your instructor publishes class times and lab deadlines.',curriculum='\n'.join(title for _,titles in MODULES for title in titles)))
