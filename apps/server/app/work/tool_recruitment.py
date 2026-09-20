"""Confirmed hiring adapts the existing M1 purchase and T2 employment transaction."""

from dataclasses import asdict

from sqlalchemy import select

from app.economy.contracts import MAX_AMOUNT, EconomyContractError
from app.models.lifecycle import ProvisioningJob
from app.services.economy.talent_trade import TalentTradeError, TalentTradeService
from app.work import contracts as C
from app.work import readiness, tools


def confirmation_terms(db, ctx, args):
    try:
        quote = TalentTradeService(db).quote_buyout(
            args["listing_id"], buyer_company_id=ctx.company_id
        )
    except TalentTradeError as exc:
        raise tools.ToolError(str(exc)) from exc
    return tools.ConfirmationTerms(
        target_ref=f"market_listing:{quote['listing_id']}",
        target_version=quote["version"],
        amount=quote["amount"],
    )


def recruit(db, ctx, args):
    # The executor has rechecked the exact approved terms and both grants. The
    # domain services remain responsible for funds, listing CAS and employment.
    try:
        _, purchase = TalentTradeService(db).create_offer(
            args["listing_id"],
            buyer_company_id=ctx.company_id,
            amount=args["amount"],
            recruitment_actor=f"employee:{ctx.employee_id}",
            commit=False,
        )
    except (RuntimeError, EconomyContractError) as exc:
        raise tools.ToolError(str(exc)) from exc
    if purchase is None:
        raise tools.ToolError("negotiation_requires_seller_acceptance")
    job = db.scalar(
        select(ProvisioningJob).where(
            ProvisioningJob.employee_id == purchase.employee_id,
            ProvisioningJob.owner == "runtime",
        )
    )
    if job is None:
        raise tools.ToolError("recruitment_provisioning_intent_missing")
    return {
        "recruitment": {
            "created": purchase.created,
            "listing_id": purchase.listing_id,
            "employee_id": purchase.employee_id,
            "person_id": purchase.person_id,
            "identity_id": purchase.identity_id,
            "company_id": purchase.buyer_company_id,
        },
        "trade": {**asdict(purchase), "status": "settled"},
        "provisioning": readiness.orchestration_result(db, job).as_dict(),
    }


def build_tool():
    return tools.ToolSpec(
        name="recruit_from_market",
        description=(
            "Propose a fixed-price market hire for this project. Read inspect_candidate for "
            "commercial_terms, then submit in a recruit decision. Both hiring and spending "
            "authority and human Owner confirmation are required. Only buyout is supported; "
            "negotiation requires seller acceptance. Hiring creates a pending provisioning "
            "intent; inspect_readiness later to determine whether the employee can work."
        ),
        side_effect=C.ToolSideEffect.high_impact,
        required_authority=C.AuthorityKind.approve_hiring,
        additional_authorities=(C.AuthorityKind.spend_credits,),
        authority_target=lambda db, ctx, args: C.AuthorityTarget(company_id=ctx.company_id),
        amount_arg="amount",
        decision_semantics=C.DecisionSemantics.required,
        confirmation_policy=tools.ConfirmationPolicy(
            decision_kind=C.DecisionKind.recruit, resolve_terms=confirmation_terms
        ),
        input_schema=tools.object_schema(
            {
                "project_id": {"type": "integer", "minimum": 1},
                "listing_id": {"type": "integer", "minimum": 1},
                "amount": {"type": "integer", "minimum": 1, "maximum": MAX_AMOUNT},
            },
            ("project_id", "listing_id", "amount"),
        ),
        output_schema=tools.object_schema(
            {name: {"type": "object"} for name in ("recruitment", "trade", "provisioning")},
            ("recruitment", "trade", "provisioning"),
        ),
        handler=recruit,
    )
