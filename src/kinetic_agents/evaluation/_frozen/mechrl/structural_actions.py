"""Unambiguous edit arguments; historical `species` retains its old meaning."""


def structural_species(operation, payload):
    if operation not in ('reduce', 'restore'):
        raise ValueError('structural operation required')
    key = 'species_to_remove' if operation == 'reduce' else 'species_to_restore'
    other = 'species_to_restore' if operation == 'reduce' else 'species_to_remove'
    if other in payload or 'keep_species' in payload or 'species_to_keep' in payload:
        raise ValueError(f'{operation} requires {key}; a retained-species list is not a deletion list')
    if 'species' in payload and key in payload:
        raise ValueError('supply only one structural species argument, not both aliases')
    supplied = key in payload or 'species' in payload
    if supplied and 'threshold' in payload:
        raise ValueError('choose explicit removed species OR a DRGEP threshold, not both')
    if supplied:
        names = payload[key] if key in payload else payload['species']
        if not isinstance(names, list) or not names or any(not isinstance(s, str) or not s for s in names) or len(set(names)) != len(names):
            raise ValueError(f'{key} requires a nonempty unique list of species names')
        return names
    return None
