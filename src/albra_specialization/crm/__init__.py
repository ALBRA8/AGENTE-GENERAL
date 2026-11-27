# ALBRA SPECIALIZATION — CRM bridge (R-AG-1, S4.4).
#
# Domain-specific hosted integration of AGENTE-GENERAL: the versioned
# dispatch bridge of CRM WhatsPower (POST /crm/dispatch). It lives in the
# specialization layer so the universal seed (src/albra_seed/) stays free
# of domain knowledge — enforced by the contamination scanner.
#
# Hosted integrations mount themselves into a FastAPI app through
# `install_crm_dispatch(app)`; the seed only exposes the generic
# `extra_extensions` hook in `albra_seed.api.app.create_app`.

from albra_specialization.crm.bridge import (
    CrmDispatchError,
    CrmDispatchEvent,
    install_crm_dispatch,
)

__all__ = ["CrmDispatchError", "CrmDispatchEvent", "install_crm_dispatch"]
