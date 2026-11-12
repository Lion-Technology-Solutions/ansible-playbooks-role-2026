"""Administrator student directory presentation; existing write controls stay authoritative."""
import csv,io
from flask import abort,g,request,Response,url_for
from sqlalchemy import select,or_,and_
from sqlalchemy.orm import joinedload


def install(app,db,m,h):
    Student,Enrollment,Archive,Identity,Proof,Manual,Invoice=(m[k] for k in ('Student','Enrollment','StudentArchive','StudentIdentity','PlanPaymentProof','ManualFeePayment','FeeInvoice'))
    def states(e,s):
        control=g.directory_controls.get(s.id)
        active=not g.directory_archived and (not control or control.active)
        tuition=g.directory_tuition.get(e.id) if e else None
        access='active' if e and active and e.status=='approved' and e.payment_status=='verified' and not (tuition and (tuition.access_hold or tuition.status in ('pending','overdue'))) else ('hold' if not active or (tuition and (tuition.access_hold or tuition.status=='overdue')) else 'locked')
        review=bool(e and (e.payment_status=='submitted' or e.id in pending_proofs()))
        payment='none' if not e else 'review' if review else 'overdue' if tuition and tuition.status=='overdue' else 'verified' if e.payment_status=='verified' else 'pending'
        return dict(payment=payment,payment_label={'none':'Not enrolled','overdue':'Overdue','verified':'Verified','review':'Review needed','pending':'Pending'}[payment],access=access,access_label={'active':'Active','hold':'On hold','locked':'Locked'}[access])
    def pending_proofs():
        if not hasattr(g,'directory_pending_proofs'):
            plan=set(db.session.scalars(select(Proof.enrollment_id).where(Proof.status.in_(('submitted','more_info')))))
            manual=set(db.session.scalars(select(Invoice.enrollment_id).join(Manual,Manual.invoice_id==Invoice.id).where(Manual.status.in_(('submitted','more_info')))))
            g.directory_pending_proofs=plan|manual
        return g.directory_pending_proofs
    def context():
        args=request.args;archived=args.get('archived')=='yes';q=args.get('q','').strip()[:100]
        program=args.get('program','');country=args.get('country','');cohort=args.get('cohort','');payment=args.get('payment','');access=args.get('access','');year=args.get('year','')
        if (year and (not year.isascii() or not year.isdigit() or len(year)!=4 or not 2000<=int(year)<=2100)) or (program and (not program.isascii() or not program.isdigit() or len(program)>10 or int(program)>2147483647)) or (cohort and (not cohort.isascii() or not cohort.isdigit() or len(cohort)>10 or int(cohort)>2147483647)) or payment not in ('','none','pending','review','verified','overdue') or access not in ('','active','locked','hold'):abort(400)
        currency=args.get('currency','USD')
        if (country and country not in m['COUNTRIES']) or currency not in m['CURRENCIES']:abort(400)
        if args.get('tab','overview') not in ('overview','payments','activity','manage'):abort(400)
        query=select(Student).outerjoin(Identity,Identity.student_id==Student.id).options(joinedload(Student.identity))
        archive_ids=select(Archive.student_id)
        query=query.where(Student.id.in_(archive_ids) if archived else ~Student.id.in_(archive_ids))
        enrollment_query=select(Enrollment.student_id)
        if program:enrollment_query=enrollment_query.where(Enrollment.course_id==int(program))
        if cohort:enrollment_query=enrollment_query.where(Enrollment.cohort_id==int(cohort))
        if program or cohort:query=query.where(Student.id.in_(enrollment_query))
        if country:query=query.where(Student.country==country)
        if year:query=query.where(db.extract('year',Student.created_at)==int(year))
        if q:
            pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            parts=[Student.name.ilike(pattern,escape='\\'),Student.email.ilike(pattern,escape='\\')]
            number=q.rsplit('-',1)[-1]
            if number.isascii() and number.isdigit() and len(number)<=10 and int(number)<=2147483647:
                exact=Identity.id==int(number)
                if q.upper().startswith('LT-'):
                    fields=q.split('-')
                    if len(fields)==3 and fields[1].isascii() and fields[1].isdigit():exact=and_(exact,db.extract('year',Student.created_at)==int(fields[1]))
                parts.append(exact)
            query=query.where(or_(*parts))
        students=list(db.session.scalars(query.order_by(Student.created_at.desc(),Student.id)))
        ids=[s.id for s in students]
        es=list(db.session.scalars(select(Enrollment).where(Enrollment.student_id.in_(ids)).options(joinedload(Enrollment.student),joinedload(Enrollment.course),joinedload(Enrollment.cohort)).order_by(Enrollment.created_at.desc(),Enrollment.id.desc()))) if ids else []
        g.directory_archived=archived
        g.directory_controls={r.student_id:r for r in db.session.scalars(select(m['StudentControl']).where(m['StudentControl'].student_id.in_(ids)))}
        g.directory_tuition={r.enrollment_id:r for r in db.session.scalars(select(m['TuitionControl']).where(m['TuitionControl'].enrollment_id.in_([e.id for e in es])))}
        grouped={sid:[] for sid in ids}
        for e in es:grouped[e.student_id].append(e)
        rows=[]
        for s in students:
            matches=[e for e in grouped[s.id] if (not program or e.course_id==int(program)) and (not cohort or e.cohort_id==int(cohort))]
            candidate=[(e,states(e,s)) for e in matches] or [(None,states(None,s))]
            candidate=[(e,state) for e,state in candidate if (not payment or state['payment']==payment) and (not access or state['access']==access)]
            if candidate:
                e,state=candidate[0];rows.append(dict(student=s,enrollment=e,enrollments=grouped[s.id],has_active=any(st['access']=='active' for _,st in candidate),**state))
        page=args.get('page','1')
        if not page.isascii() or not page.isdigit() or len(page)>8:abort(400)
        page=max(1,int(page));size=6;pages=max(1,(len(rows)+size-1)//size);page=min(page,pages)
        stats=dict(total=len(rows),active=sum(r['has_active'] for r in rows),pending=sum(r['payment']=='pending' for r in rows),review=sum(r['payment']=='review' for r in rows))
        visible=rows[(page-1)*size:page*size];selected=None
        if args.get('student'):
            selected=next((r for r in rows if r['student'].id==args['student']),None)
            if not selected:abort(404)
        proofs=[];entries=[]
        if selected:
            eids=[e.id for e in selected['enrollments']]
            for proof in db.session.scalars(select(Proof).where(Proof.enrollment_id.in_(eids)).order_by(Proof.created_at.desc())):
                proofs.append(dict(name=proof.receipt_name,reference=proof.reference,status=proof.status,course=proof.enrollment.course.name,created_at=proof.created_at,url=url_for('plan_receipt',proof_id=proof.id)))
            for proof in db.session.scalars(select(Manual).join(Invoice,Manual.invoice_id==Invoice.id).where(Invoice.enrollment_id.in_(eids)).order_by(Manual.created_at.desc())):
                proofs.append(dict(name=proof.receipt_name,reference=proof.reference,status=proof.status,course=proof.invoice.enrollment.course.name,created_at=proof.created_at,url=url_for('receipt_file',payment_id=proof.id)))
            entries=list(db.session.scalars(select(m['PaymentEntry']).where(m['PaymentEntry'].enrollment_id.in_(eids)).order_by(m['PaymentEntry'].received_at.desc())))
        activity=[]
        if selected:
            for record in db.session.scalars(select(m['StudentManagementAudit']).where(m['StudentManagementAudit'].student_id==selected['student'].id).order_by(m['StudentManagementAudit'].created_at.desc()).limit(20)):
                activity.append(dict(title=record.action.replace('_',' ').title(),detail=record.detail,at=record.created_at))
            for proof in proofs:activity.append(dict(title='Payment receipt submitted',detail=proof['course']+' · '+proof['reference'],at=proof['created_at']))
            activity.sort(key=lambda item:m['aware'](item['at']),reverse=True)
        def href(**changes):
            values={key:value for key,value in args.items() if key in ('q','program','country','cohort','payment','access','year','currency','archived','page','student','tab')};values.update(changes);return url_for('admin',view='students',**{key:value for key,value in values.items() if value not in ('',None)})
        return dict(overview=dict(program=program,country=country,year=year,currency=currency),activity=activity[:30],rows=visible,all_rows=rows,selected=selected,proofs=proofs,entries=entries,stats=stats,page=page,pages=pages,start=(page-1)*size+1 if rows else 0,end=min(page*size,len(rows)),href=href,q=q,payment=payment,access=access,cohort=cohort,archived=archived)
    @app.get('/admin/students/export.csv')
    @h['admin_required']
    def students_export():
        directory=context();selected=set(request.args.getlist('id'))
        if len(selected)>100:abort(400)
        out=io.StringIO();writer=csv.writer(out);writer.writerow(['Student number','Name','Email','Country','Program','Cohort','Payment','Access'])
        for row in directory['all_rows']:
            s=row['student'];e=row['enrollment']
            if selected and s.id not in selected:continue
            writer.writerow([m['csv_safe'](str(value)) for value in (s.student_number,s.name,s.email,s.country,e.course.name if e else '',e.cohort.start_date.isoformat() if e and e.cohort else '',row['payment_label'],row['access_label'])])
        return Response(out.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="liontech-students.csv"','Cache-Control':'no-store'})
    app.extensions['students_console']=dict(context=context)
