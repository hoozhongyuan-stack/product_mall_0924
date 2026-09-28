"""Revisioned marketing parameters. Readers capture once for a quote/order."""
import hashlib
import json
from uuid import UUID
from django.db import connection, transaction
from catalog.models import MemberGrade
from customers.consumption import snapshot_grade_thresholds
from customers.models import GradeThreshold
from .models import PointsPolicy
from .service import BenefitError

FIELDS = {'earnUnitFen':'earn_unit_fen','earnPoints':'earn_points',
          'deductPoints':'deduct_points','deductFen':'deduct_fen','maxPercent':'max_percent',
          'validDays':'valid_days','refundValidDays':'refund_valid_days'}


def current_policy(*, lock=False):
    PointsPolicy.objects.get_or_create(pk=1)
    query = PointsPolicy.objects.select_for_update() if lock else PointsPolicy.objects
    row = query.get(pk=1)
    return {'revision':row.revision, **{key:getattr(row,value) for key,value in FIELDS.items()},
            'gradeThresholds':snapshot_grade_thresholds()}


def rules_data():
    policy=current_policy()
    thresholds={row['gradeId']:row['minimumSpendFen'] for row in policy['gradeThresholds']}
    return {'revision':policy['revision'],
        'points':{key:policy[key] for key in FIELDS},
        'grades':[{'id':str(row.id),'code':row.code,'name':row.name,'rank':row.rank,
                   'minimumSpendFen':thresholds.get(str(row.id),0)}
                  for row in MemberGrade.objects.filter(enabled=True).order_by('rank','id')]}


def validate_rules(body):
    if not isinstance(body,dict) or set(body)!={'expectedRevision','points','grades','reason'}:
        raise BenefitError('等级与积分规则字段不完整。')
    revision,points,grades,reason=(body[key] for key in ['expectedRevision','points','grades','reason'])
    if type(revision) is not int or revision<1:
        raise BenefitError('规则修订号格式不正确。')
    if not isinstance(points,dict) or set(points)!=set(FIELDS):
        raise BenefitError('请填写完整的积分参数。')
    for key,value in points.items():
        upper=100 if key=='maxPercent' else 3650 if key in {'validDays','refundValidDays'} else 1000000
        if type(value) is not int or not 1<=value<=upper:
            raise BenefitError(f'{key} 必须为 1 至 {upper} 的整数。')
    if not isinstance(reason,str) or not 1<=len(reason.strip())<=200:
        raise BenefitError('请填写 1 至 200 字的变更原因。')
    enabled=list(MemberGrade.objects.filter(enabled=True).order_by('rank','id'))
    if not isinstance(grades,list) or len(grades)!=len(enabled):
        raise BenefitError('请提交所有启用等级的门槛。')
    thresholds={}
    for row in grades:
        if not isinstance(row,dict) or set(row)!={'id','minimumSpendFen'}:
            raise BenefitError('等级门槛字段不正确。')
        try: grade_id=UUID(str(row['id']))
        except (ValueError,TypeError): raise BenefitError('等级 ID 不正确。')
        value=row['minimumSpendFen']
        if grade_id in thresholds or type(value) is not int or not 0<=value<=9000000000000:
            raise BenefitError('等级门槛须为范围内的整数分，且等级不可重复。')
        thresholds[grade_id]=value
    if set(thresholds)!={row.id for row in enabled} or not enabled:
        raise BenefitError('等级已变化，请刷新。','REVISION_CONFLICT',409)
    values=[thresholds[row.id] for row in enabled]
    if values[0]!=0 or any(b<=a for a,b in zip(values,values[1:])):
        raise BenefitError('基础等级门槛须为 0，后续等级门槛须按等级顺序严格增加。')
    return points,thresholds,reason.strip()


def update_rules_locked(body):
    if not connection.in_atomic_block: raise RuntimeError('Rules require caller transaction.')
    policy=current_policy(lock=True)
    points,thresholds,_=validate_rules(body)
    if policy['revision']!=body['expectedRevision']:
        raise BenefitError('规则已变化，请刷新后核对。','REVISION_CONFLICT',409)
    row=PointsPolicy.objects.get(pk=1)
    for key,attr in FIELDS.items(): setattr(row,attr,points[key])
    row.revision+=1
    row.save()
    for grade_id,value in thresholds.items():
        GradeThreshold.objects.update_or_create(grade_id=grade_id,
            defaults={'minimum_spend_fen':value,'revision':row.revision})
    return rules_data()


def body_digest(body):
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
