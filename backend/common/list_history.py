"""Pure list filter parsing, with domain-selected validation errors."""
from datetime import date

def history_options(params, prefix, statuses, error_type=ValueError):
    keys=[prefix+suffix for suffix in ('Page','PageSize','Search','Status','DateFrom','DateTo')]
    if any(len(params.getlist(key))>1 for key in keys):
        raise error_type('历史记录筛选参数不可重复。')
    page=int(params.get(prefix+'Page',1)); size=int(params.get(prefix+'PageSize',20))
    if not 1<=page<=10000 or size not in (20,50,100):
        raise error_type('历史记录分页参数无效。')
    status=params.get(prefix+'Status',''); search=params.get(prefix+'Search','').strip()
    if len(search)>120 or (status and status not in statuses):
        raise error_type('历史记录检索或状态无效。')
    start=params.get(prefix+'DateFrom',''); end=params.get(prefix+'DateTo','')
    try:
        start=date.fromisoformat(start) if start else None
        end=date.fromisoformat(end) if end else None
    except ValueError:
        raise error_type('历史记录日期无效。') from None
    if end==date.max:
        raise error_type('结束日期超出可支持范围。')
    if start and end and start>end:
        raise error_type('开始日期不能晚于结束日期。')
    return {'page':page,'pageSize':size,'search':search,'status':status,'start':start,'end':end}
