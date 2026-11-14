#!/usr/bin/env python3
"""Bounded portal benchmark using disposable local fixtures, never production data."""
import argparse,contextlib,json,math,os,statistics,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--students',type=int,default=500)
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 0<=args.students<=2000 or not 1<=args.samples<=20:parser.error('Use 0–2000 example students and 1–20 samples.')
    root=args.source_root.resolve()
    os.environ.update(SCHOOL_TESTING='1',DATABASE_URL='sqlite:///:memory:',SCHOOL_SECRET_KEY='quality-benchmark-only',SCHOOL_ADMIN_PASSWORD='test-admin-password')
    sys.path.insert(0,str(root/'school-portal/tests'));sys.path.insert(0,str(root/'school-portal'))
    import conftest
    from app import db,Student,Enrollment,Lesson,create_app,AuthPolicy,now
    from localization import CURRENCIES
    from sqlalchemy import select,event
    from test_portal import register,analytics
    from test_staff_portal import setup,create,login
    temporary=TemporaryDirectory(prefix='liontech-quality-')
    app=create_app({'TESTING':True,'BONANZA_ENABLED':False,'PAYMENT_RETENTION_ENABLED':False,'DATA_ANALYTICS_LINK_PLAN':False,'SESSION_COOKIE_SECURE':False,'SQLALCHEMY_DATABASE_URI':'sqlite:///'+str(Path(temporary.name)/'quality.db'),'WHATSAPP_TEMPLATE_VALIDATOR':lambda *args:{'type':'twilio/text','approval':'approved'}})
    rates={code:'1' for code in CURRENCIES};rates.update(USD='1',CAD='1.4',XAF='600',NGN='1600',EUR='.9',GBP='.8',GHS='15',JPY='150');app.config['FX_PROVIDER']=lambda:(rates,now())
    with app.app_context():
        db.create_all();db.session.add(AuthPolicy(id=1,require_email=False,require_mfa=False));db.session.commit()
    with app.app_context():
        if not app.testing or db.engine.dialect.name!='sqlite':raise RuntimeError('Only disposable SQLite test fixtures are supported.')
        engine=db.engine
    manager,sent=setup(app);member=create(app,'Benchmark Officer');staff=login(app,sent,member)
    student=app.test_client();register(student,email='benchmark-student@example.test');cid,cohort=analytics(app)
    with app.app_context():
        person=db.session.scalar(select(Student).where(Student.email=='benchmark-student@example.test'))
        db.session.add(Enrollment(student_id=person.id,course_id=cid,cohort_id=cohort,status='approved',payment_status='verified'))
        lesson=Lesson(course_id=cid,title='Benchmark lesson',published=True,body='Example learning resources');db.session.add(lesson);db.session.commit();lid=lesson.id
    queries=[]
    def count(*unused):queries.append(1)
    event.listen(engine,'before_cursor_execute',count)
    public=app.test_client();rows=[]
    def measure(label,client,path):
        client.get(path);elapsed=[];counts=[]
        for _ in range(args.samples):
            queries.clear();start=time.perf_counter();response=client.get(path);elapsed.append((time.perf_counter()-start)*1000);counts.append(len(queries))
            if response.status_code!=200:raise RuntimeError(f'{label}: HTTP {response.status_code}')
        item=dict(label=label,path=path,status=response.status_code,decoded_bytes=len(response.data),median_ms=round(statistics.median(elapsed),2),p95_ms=round(sorted(elapsed)[math.ceil(len(elapsed)*.95)-1],2),queries=max(counts));rows.append(item);print(json.dumps(item),flush=True)
    try:
        for label,client,path in [('public login',public,'/student/login'),('student home',student,'/portal'),('student lesson',student,f'/lessons/{lid}'),('admin students small',manager,'/admin?view=students'),('admin lesson assignment',manager,'/admin/lesson-tasks'),('admin lesson reviews',manager,'/admin/lesson-submissions'),('staff home',staff,'/staff'),('staff task board',staff,'/staff/workboard')]:measure(label,client,path)
        with app.app_context():
            db.session.add_all([Student(name=f'Benchmark learner {i:04d}',email=f'benchmark-{i}@example.test',phone='+15555555555',country='Canada',kind='new',password_hash='test-only-account-disabled') for i in range(args.students)]);db.session.commit()
        measure(f'admin students {args.students}',manager,'/admin?view=students')
        event.remove(engine,'before_cursor_execute',count)
        def read_request(_):
            client=app.test_client();start=time.perf_counter();response=client.get('/student/login');return response.status_code,(time.perf_counter()-start)*1000
        start=time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as executor:results=list(executor.map(read_request,range(24)))
        duration=time.perf_counter()-start;times=sorted(t for _,t in results)
        load=dict(requests=24,workers=4,successful=sum(s==200 for s,_ in results),median_ms=round(statistics.median(times),2),p95_ms=round(times[math.ceil(len(times)*.95)-1],2),requests_per_second=round(24/duration,2))
        if load['successful']!=24:raise RuntimeError('Bounded concurrent read check failed.')
        result=dict(environment='Disposable file-backed SQLite test fixture; not a production capacity or browser Core Web Vitals measurement.',pages=rows,concurrent_reads=load)
        print(json.dumps({'concurrent_reads':load}),flush=True)
        if args.output:args.output.write_text(json.dumps(result,indent=2))
    finally:
        with contextlib.suppress(Exception):event.remove(engine,'before_cursor_execute',count)
        with app.app_context():db.session.remove();db.engine.dispose()
        temporary.cleanup()


if __name__=='__main__':main()
