from __future__ import annotations

from fake_homeassistant_v2.models import HandlerResult


def fan_set_percentage(ctx):
    entity_id = ctx.target_entity_ids[0]
    percentage = int(ctx.payload["percentage"])
    state = "on" if percentage > 0 else "off"
    ctx.runtime.set_state(
        entity_id,
        state=state,
        attributes={"percentage": percentage},
        context=ctx.context,
    )
    return HandlerResult(changed_entity_ids=[entity_id], response={"percentage": percentage})
