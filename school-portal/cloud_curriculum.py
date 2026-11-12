"""Approved AWS-led Cloud Computing pathway, with Azure and Google Cloud comparisons."""
from decimal import Decimal, ROUND_HALF_UP

STAGES=[
 dict(key='foundation',slug='cloud-computing',name='Foundation',weeks=12,price='199.00',summary='Understand cloud concepts and deploy a small, secure application on AWS.',titles=[
 'Introduction to Cloud Computing','AWS, Azure and Google Cloud','Linux and Networking Essentials','Cloud Accounts, Identity and Access','Regions, Availability Zones and Shared Responsibility','Virtual Machines and Cloud Compute','Cloud Storage and Backups','Virtual Networks and Firewalls','Databases and Managed Services','Monitoring, Billing and Cost Control','Azure and Google Cloud Guided Introduction','Foundation Project and Presentation'],activities=[
 'Compare traditional infrastructure with cloud services.','Map equivalent services across the three providers.','Practise commands, IP addressing, DNS and connectivity.','Configure a training account, MFA and basic permissions.','Choose a deployment location and identify security responsibilities.','Launch and connect to an AWS EC2 instance.','Create protected object storage and practise recovery.','Build a basic AWS VPC with controlled access.','Connect a simple application to a managed database.','Create monitoring alerts and a spending checklist.','Explore Azure Virtual Machines and Google Compute Engine.','Deploy and explain a small AWS application.']),
 dict(key='intermediate',slug='cloud-computing-intermediate',name='Intermediate',weeks=12,price='299.00',summary='Operate reliable cloud environments and troubleshoot common problems.',titles=[
 'Cloud Administration and Operational Standards','Identity Administration and Least Privilege','Networking, Routing and Private Connectivity','Load Balancing and Automatic Scaling','Storage Administration and Lifecycle Management','Database Administration and Resilience','Monitoring, Logging and Troubleshooting','Infrastructure as Code with Terraform','Containers and Managed Application Platforms','Azure Administration Comparison','Google Cloud Administration Comparison','Operations Project and Incident Exercise'],activities=[
 'Create an inventory, naming convention and tagging plan.','Configure role-based access for a sample team.','Build public and private subnets with controlled routing.','Deploy an application across multiple instances.','Configure storage policies, versioning and recovery.','Practise database backups and restoration.','Investigate a simulated application outage.','Provision a repeatable AWS environment.','Package and deploy a containerized application.','Apply networking, identity and monitoring concepts in Azure.','Apply equivalent operational concepts in Google Cloud.','Restore a service and submit an operational handover.']),
 dict(key='advanced',slug='cloud-computing-advanced',name='Advanced',weeks=12,price='399.00',summary='Design, automate, secure and explain a production-style cloud solution.',titles=[
 'Architecture Requirements and Trade-offs','Resilient and Highly Available Architecture','Advanced Networking and Hybrid Connectivity','Cloud Governance and Multiple-account Strategy','Advanced Identity, Secrets and Encryption','Reusable Terraform and Deployment Automation','Kubernetes and Container Architecture','Serverless and Event-driven Architecture','Disaster Recovery and Continuity','Performance and Cost Optimization','AWS, Azure and Google Cloud Architecture Comparison','Final Architecture Capstone and Presentation'],activities=[
 'Turn a business scenario into technical requirements.','Design an application that tolerates component failures.','Plan connectivity between cloud and on-premises environments.','Design account boundaries, policies and ownership.','Implement protected application access and secret handling.','Build reusable infrastructure modules.','Deploy a guided workload to a prepared Kubernetes environment.','Build an application using functions, events and queues.','Define recovery objectives and test a recovery procedure.','Compare architecture choices against a workload and budget.','Map an AWS design to equivalent Azure and Google Cloud services.','Demonstrate a solution and defend its design decisions.'])
]
CLOUD_SLUGS=tuple(s['slug'] for s in STAGES)

def installments(total):
    total=Decimal(str(total));first=(total/3).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    return [first,first,total-first*2]

def seed_cloud(connection,m):
    from sqlalchemy import select
    Course,Profile,Pub,Price,Policy,Lesson,Unit=(m[k] for k in ('Course','ProgramProfile','CoursePublication','GlobalTuition','CoursePaymentPolicy','Lesson','CloudUnit'))
    for stage in STAGES:
        cid=connection.execute(select(Course.id).where(Course.slug==stage['slug'])).scalar()
        if not cid:
            cid=connection.execute(Course.__table__.insert().values(slug=stage['slug'],name='Cloud Computing · '+stage['name'],description=stage['summary'],skills='AWS · Azure · Google Cloud · Cloud engineering',registration_url='',resources_url='').returning(Course.id)).scalar_one()
        profile=connection.execute(select(Profile.__table__).where(Profile.course_id==cid)).mappings().first()
        values=dict(duration='12 weeks',schedule='Three hours of live lectures plus six hours of instructor-guided labs and projects weekly. Sessions are published by the instructor.',curriculum='\n'.join(stage['titles']))
        if not profile:connection.execute(Profile.__table__.insert().values(course_id=cid,tuition={},**values))
        elif not profile['duration']:connection.execute(Profile.__table__.update().where(Profile.course_id==cid).values(**values))
        if not connection.execute(select(Pub.course_id).where(Pub.course_id==cid)).first():
            connection.execute(Pub.__table__.insert().values(course_id=cid,published=True,published_at=m['now'](),payment_links={}))
        if not connection.execute(select(Price.course_id).where(Price.course_id==cid)).first():
            connection.execute(Price.__table__.insert().values(course_id=cid,currency='USD',total=Decimal(stage['price']),installments=3,full_url='',installment_url=''))
        if not connection.execute(select(Policy.course_id).where(Policy.course_id==cid)).first():
            parts=[dict(label=['First monthly installment — enrollment included','Second monthly installment','Third monthly installment'][i],amount=str(amount),timing=['7th of your starting month; immediately if already passed','7th of the following month','7th of the third month'][i]) for i,amount in enumerate(installments(stage['price']))]
            connection.execute(Policy.__table__.insert().values(course_id=cid,currency='USD',total=Decimal(stage['price']),checkout_url='',stages=parts,enabled=True))
        for number,(title,activity) in enumerate(zip(stage['titles'],stage['activities']),1):
            if connection.execute(select(Unit.lesson_id).where(Unit.course_id==cid,Unit.number==number)).first():continue
            lid=connection.execute(Lesson.__table__.insert().values(course_id=cid,title=title,module=stage['name']+' · Week '+str(number),body='Learning focus: '+title+'.\nGuided practical activity: '+activity+'\nYour instructor will publish supporting notes, resources and submission deadlines. Use the training environment assigned by your instructor and follow its cost controls.',position=number,published=False,available_at=m['now']()).returning(Lesson.id)).scalar_one()
            connection.execute(Unit.__table__.insert().values(course_id=cid,number=number,lesson_id=lid))
