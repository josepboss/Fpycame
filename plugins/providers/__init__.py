"""Account supplier API clients (SMM services retain their separate workflow)."""
from .accounts import AccountSupplier, get_provider


def list_providers() -> list[str]:
    return ['hstore', 'dark', 'djekxa', 'smmcost']
