"""Reserve space for the complete replacement policy in both listing languages."""
from locales.localizer import Localizer

LIMIT = 817
POLICY_VERSION = 1


def imported_description(text: str, language: str = 'en') -> str:
    policy = Localizer().translate('oi_replacement_policy',language=language)
    text = text.replace('\r\n','\n').replace('\r','\n').strip()
    # Repeated previews/publish attempts must not duplicate the policy.
    if policy in text:
        text = text.split(policy,1)[0].rstrip()
    budget = LIMIT-len(policy)-2
    body = text[:budget].rstrip()
    return body+'\n\n'+policy if body else policy
