"""Bounded evidence views; complete numerical artifacts remain unchanged."""

def compact_evidence(result):
    omitted = {}
    def walk(value, path=''):
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                location = path+'.'+key if path else key
                if key in ('bls_statistics', 'mean_image', 'difference_image', 'aperture', 'phase_days', 'flux') and isinstance(item, (list, dict)):
                    omitted[location] = {'count': len(item), 'retained': 0}
                else:
                    output[key] = walk(item, location)
            return output
        if isinstance(value, list):
            if len(value) > 20:
                omitted[path] = {'count': len(value), 'retained': 20}
            return [walk(item, f'{path}[{i}]') for i, item in enumerate(value[:20])]
        return value
    compact = walk(result)
    compact['omitted_or_truncated'] = omitted
    return compact


def signal_groups(investigations):
    """Suggested same-target, near-period groups, never an astrophysical identity."""
    groups = []
    for record in investigations:
        star = record.get('star', {})
        target_key = ('TIC '+str(star['tic_id'])) if star.get('tic_id') is not None else record['target'].strip().lower()
        for signal in record.get('signals', []):
            group = next((g for g in groups if g['target_key'] == target_key and
                          abs(signal['period']/g['reference_period']-1) < .005), None)
            if group is None:
                group = {'target_key': target_key, 'reference_period': signal['period'], 'members': []}
                groups.append(group)
            group['members'].append({'investigation_id': record['id'], 'signal_index': signal['index'],
                'period': signal['period'], 'classification': signal['classification']})
    return {'groups': groups, 'group_count': len(groups),
            'caveat': 'Suggested grouping within 0.5% of the first member period for the same catalog target. Aliases remain separate. Close planets may be grouped; measurements are never merged.'}
