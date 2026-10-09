"""Explicit unit specification configuration and server-derived SKU units."""
from .validation import CatalogError, number_field, object_field, list_field, text_field


def parse_conversion(raw, axes):
    """Normalize client keys; never infer unit semantics from display names."""
    if raw is None:
        return None
    raw = object_field(raw, '多单位换算')
    if set(raw) != {'axisKey', 'baseOptionKey', 'ratios'}:
        raise CatalogError('多单位换算字段不正确。')
    for axis_row in axes:
        for key in [axis_row['clientKey'], *[option['clientKey'] for option in axis_row['options']]]:
            if key != text_field(key, '规格标识', 80):
                raise CatalogError('多单位规格标识不能包含首尾空格。')
    axis_key = text_field(raw['axisKey'], '单位规格项标识', 80)
    base_key = text_field(raw['baseOptionKey'], '基本单位规格值标识', 80)
    axis = next((axis for axis in axes if axis['clientKey'] == axis_key), None)
    if not axis:
        raise CatalogError('单位规格项不存在。')
    options = {option['clientKey']: option['value'] for option in axis['options']}
    if base_key not in options:
        raise CatalogError('基本单位必须属于单位规格项。')
    ratios = {}
    for item in list_field(raw['ratios'], '单位换算比例', 20, 1):
        item = object_field(item, '单位换算比例')
        if set(item) != {'optionKey', 'ratio'} or not isinstance(item['optionKey'], str):
            raise CatalogError('单位换算比例字段不正确。')
        key = item['optionKey']
        if key not in options or key in ratios:
            raise CatalogError('单位换算比例须与单位规格值一一对应。')
        ratios[key] = number_field(item['ratio'], '单位换算比例', 1, 1000000000)
        text_field(options[key], '单位名称', 30)
    if set(ratios) != set(options) or ratios[base_key] != 1:
        raise CatalogError('每个单位必须设置换算比例，基本单位比例必须为 1。')
    return {'axisKey': axis['clientKey'], 'baseOptionKey': base_key,
            'ratios': [{'optionKey': key, 'ratio': ratios[key]} for key in sorted(ratios)]}


def derived_unit(config, axes, keys, provided=None):
    from .service import parse_unit
    if config is None:
        return parse_unit(provided)
    axis = next(axis for axis in axes if axis['clientKey'] == config['axisKey'])
    options = {option['clientKey']: text_field(option['value'], '单位名称', 30) for option in axis['options']}
    selected = [key for key in keys if key in options]
    if len(selected) != 1:
        raise CatalogError('每个 SKU 必须选择一个单位。')
    key = selected[0]
    ratios = {row['optionKey']: row['ratio'] for row in config['ratios']}
    unit = {'base_unit': options[config['baseOptionKey']], 'sale_unit': options[key], 'ratio': ratios[key]}
    if provided is not None and parse_unit(provided) != unit:
        raise CatalogError('SKU 单位必须与商品单位换算配置一致。')
    return unit


def persisted_conversion(config, axis_ids, option_ids):
    if config is None:
        return None
    return {'axisKey': str(axis_ids[config['axisKey']]),
            'baseOptionKey': str(option_ids[config['baseOptionKey']]),
            'ratios': [{'optionKey': str(option_ids[row['optionKey']]), 'ratio': row['ratio']}
                       for row in config['ratios']]}


def grouped_rows(config, rows):
    """Group normalized specification selections excluding the unit axis."""
    if config is None:
        return [[row] for row in rows]
    groups = {}
    for row in rows:
        key = tuple(sorted(str(option) for axis, option in row['selection']
                           if str(axis) != config['axisKey']))
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if not any(config['baseOptionKey'] in [str(option) for _, option in row['selection']] for row in group):
            raise CatalogError('每个实物规格组合必须包含基本单位 SKU。')
    return list(groups.values())


def same_conversion(first, second):
    def canonical(config):
        if config is None:
            return None
        return (config['axisKey'], config['baseOptionKey'],
                tuple(sorted((row['optionKey'], row['ratio']) for row in config['ratios'])))
    return canonical(first) == canonical(second)


def validate_single_unit(unit, previous=None):
    if previous and (previous.base_unit, previous.sale_unit, previous.ratio) == (
            unit['base_unit'], unit['sale_unit'], unit['ratio']):
        return
    if unit['ratio'] != 1 or unit['base_unit'] != unit['sale_unit']:
        raise CatalogError('未开启多单位换算时，销售单位须与基本单位相同，比例为 1。')
