"""Mandatory search scope; client confirmation never overrides tenant identity."""
import re
from .documents import MODALITIES, normalize_modality


class ModalityRequired(ValueError):
    code = 'ModalityRequired'


class ModalityConflict(ValueError):
    code = 'ModalityConflict'

    def __init__(self, selected, detected):
        self.selected, self.detected = selected, detected
        super().__init__(f'The editor is {selected}, but your request specifies {detected}. Confirm which modality to use.')


def resolve_modality(request):
    detected = [m for m, pattern in MODALITIES.items()
                if re.search(pattern, str(request.get('prompt') or ''), re.I)]
    selected = normalize_modality(request.get('modality'))
    if selected not in MODALITIES:
        selected = None
    if len(detected) > 1:
        raise ModalityRequired('Your request mentions multiple modalities. Request one examination at a time.')
    explicit = detected[0] if detected else None
    if selected and explicit and selected != explicit:
        confirmed = normalize_modality(request.get('confirmedModality'))
        if confirmed not in (selected, explicit):
            raise ModalityConflict(selected, explicit)
        return confirmed
    modality = explicit or selected
    if not modality:
        raise ModalityRequired('Choose a modality or include one in your request before searching.')
    return modality


def tenant_prefixes(cfg):
    tenant = cfg.get('tenantId', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', tenant):
        raise ValueError('A valid authenticated hospital is required.')
    return tuple(prefix.rstrip('/') + '/' + tenant + '/' for prefix in (cfg['prefix'], cfg['finalPrefix']))
