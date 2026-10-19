"""require_agent / require_customer, llamadas directamente (sin HTTP).

Todavía ninguna ruta usa require_agent (la cola humana llega en el S5); el 403
de require_customer por HTTP se prueba en tests/api/test_tickets.py.
"""

import pytest

from revision_rag.api.deps import require_agent, require_customer
from revision_rag.core.exceptions import ForbiddenError
from revision_rag.models import User
from revision_rag.models.enums import UserRole

CUSTOMER = User(email="cliente@test.com", hashed_password="x", role=UserRole.CUSTOMER)
AGENT = User(email="agente@test.com", hashed_password="x", role=UserRole.AGENT)


async def test_require_agent() -> None:
    assert await require_agent(AGENT) is AGENT
    with pytest.raises(ForbiddenError):
        await require_agent(CUSTOMER)


async def test_require_customer() -> None:
    assert await require_customer(CUSTOMER) is CUSTOMER
    with pytest.raises(ForbiddenError):
        await require_customer(AGENT)
