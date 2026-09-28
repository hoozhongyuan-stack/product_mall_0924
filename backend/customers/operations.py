"""Privacy-safe membership and unified event read boundary. No balance writes."""
from datetime import timedelta
from django.db.models import (Sum, Min, Q, F, Value, BigIntegerField, CharField,
                              OuterRef, Exists)
from django.utils import timezone
from benefits.models import PointsAccount, PointsGrant, PointsEvent, BenefitLedger
from .models import MemberConsumption, ConsumptionEvent


def public_cause(ref, fallback='ORDER_SETTLEMENT'):
    prefix = (ref or '').split(':', 1)[0]
    return {'case':'REFUND_COMPLETED', 'reverse':'FULFILLMENT_REVERSED',
            'receipt':'ORDER_COMPLETED', 'auto-receipt':'ORDER_COMPLETED',
            'redeem':'ORDER_COMPLETED', 'expire':'POINTS_EXPIRED'}.get(prefix, fallback)


def grade_data(grade):
    return {'id':str(grade.id),'code':grade.code,'name':grade.name,'rank':grade.rank}


def points_summary(member_ids):
    at=timezone.now()
    accounts={row.member_id:row for row in PointsAccount.objects.filter(member_id__in=member_ids)}
    lots={row['member_id']:row for row in PointsGrant.objects.filter(member_id__in=member_ids)
          .values('member_id').annotate(
              pending=Sum('available_points',filter=Q(expires_at__lte=at)),
              valid=Sum('available_points',filter=Q(expires_at__gt=at)),
              expiring=Sum('available_points',filter=Q(expires_at__gt=at,expires_at__lte=at+timedelta(days=30))),
              next_expiry=Min('expires_at',filter=Q(expires_at__gt=at,available_points__gt=0)))}
    result={}
    for member_id in member_ids:
        account=accounts.get(member_id); lot=lots.get(member_id,{})
        settled=account.settled_points if account else 0
        frozen=account.frozen_points if account else 0
        pending=lot.get('pending') or 0
        available=min(max(0,settled-frozen-pending),lot.get('valid') or 0)
        result[member_id]={'settledPoints':settled,'frozenPoints':frozen,
            'availablePoints':available,'debtPoints':max(0,-settled),
            'expiredPendingPoints':pending,'expiringPoints':min(available,lot.get('expiring') or 0),
            'nextExpiryAt':lot['next_expiry'].isoformat() if lot.get('next_expiry') else None}
    return result


def member_rows(members):
    members=list(members)
    ids=[member.id for member in members]
    points=points_summary(ids)
    spend={row.member_id:row for row in MemberConsumption.objects.filter(member_id__in=ids)}
    return [{'id':str(member.id),'grade':grade_data(member.grade),'enabled':member.enabled,
             'createdAt':member.created_at.isoformat(),
             'effectiveSpendFen':spend[member.id].effective_spend_fen if member.id in spend else 0,
             'gradeEffectiveAt':spend[member.id].grade_effective_at.isoformat()
                 if member.id in spend and spend[member.id].grade_effective_at else None,
             'gradePolicyRevision':spend[member.id].grade_policy_revision if member.id in spend else 0,
             'points':points[member.id]} for member in members]


def point_rows(member_id,page,page_size):
    # EARN and RETURN have a grant event and a lifecycle explanation; show one.
    decorated_grants=BenefitLedger.objects.filter(member_id=member_id,kind__in=['EARN','RETURN']).values('grant_id')
    events=PointsEvent.objects.filter(member_id=member_id).exclude(kind='GRANT',grant_id__in=decorated_grants)
    lifecycle=BenefitLedger.objects.filter(member_id=member_id,kind__in=['EARN','RETURN','CLAWBACK','EXPIRE'])
    fields=['id','kind','amount','order_id','created_at','expires_at','balance','source_ref','cause_ref']
    event_query=events.annotate(expires_at=F('grant__expires_at'),
        balance=Value(None,output_field=BigIntegerField()),source_ref=Value('',output_field=CharField()),
        cause_ref=Value('',output_field=CharField())).values(*fields)
    life_query=lifecycle.annotate(expires_at=F('grant__expires_at')).values(*fields)
    combined=event_query.union(life_query,all=True).order_by('-created_at','-id')
    total=combined.count()
    rows=[]
    for row in combined[(page-1)*page_size:page*page_size]:
        kind=row['kind']
        amount=-row['amount'] if kind=='CONSUME' else row['amount']
        rows.append({'id':str(row['id']),'kind':kind,'amount':amount,'balance':row['balance'],
            'orderId':str(row['order_id']) if row['order_id'] else None,
            # Trusted source refs can contain internal issuance names; never expose them.
            'sourceRef':public_cause(row['cause_ref'], kind),'createdAt':row['created_at'].isoformat(),
            'expiresAt':row['expires_at'].isoformat() if row['expires_at'] else None,
            'effect':'FREEZE' if kind=='RESERVE' else 'RELEASE' if kind=='RELEASE' else 'BALANCE'})
    return rows,total


def consumption_rows(member_id,page,page_size):
    from catalog.models import MemberGrade
    query=ConsumptionEvent.objects.filter(member_id=member_id).order_by('-created_at','-id')
    total=query.count(); rows=list(query[(page-1)*page_size:page*page_size])
    ids={row.grade_id_before for row in rows}|{row.grade_id_after for row in rows}
    grades={row.id:grade_data(row) for row in MemberGrade.objects.filter(pk__in=ids)}
    return [{'id':str(row.id),'orderId':str(row.order_id),'amountFen':row.amount_fen,
        'balanceFen':row.balance_fen,'gradeBefore':grades.get(row.grade_id_before),
        'gradeAfter':grades.get(row.grade_id_after),'sourceRef':public_cause(row.source_ref),
        'createdAt':row.created_at.isoformat()} for row in rows],total
