"""Model-layer developer CLI: ``python -m app.llm.cli <command>``.

Inspect the registry (providers, tasks, prompts), run individual model tasks against a
seeded ticket or a demo fixture with the deterministic mock provider, run a demo, and
show persisted model-call statistics. Output never includes API keys, unredacted PII,
raw embeddings, hidden reasoning or full untrusted prompts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.llm.demo_fixtures import DEMO_FIXTURES, get_demo_fixture
from app.llm.providers.factory import build_providers
from app.llm.service import ModelService, ModelTaskRequest, ModelTaskResult
from app.llm.tasks import builders
from app.llm.tasks.definitions import TASK_DEFINITIONS
from app.models.customer import Customer
from app.models.enums import MessageSender
from app.models.model_call import ModelCall
from app.models.ticket import Ticket, TicketMessage
from app.prompts.registry import get_prompt_registry


def _print_result(result: ModelTaskResult) -> None:
    print(f"task              {result.task_type.value}")
    print(f"prompt            {result.prompt_name}@{result.prompt_version}")
    print(f"requested/actual  {result.requested_provider} -> {result.provider}")
    print(
        f"fallback          from={result.fallback_from} "
        f"reason={result.fallback_reason}"
    )
    print(f"attempts/repairs  {result.attempts}/{result.repair_count}")
    print(
        "tokens            "
        f"in={result.token_usage.input_tokens} "
        f"out={result.token_usage.output_tokens} "
        f"src={result.token_usage.source.value}"
    )
    print(
        f"cost              {result.cost.microunits} µGBP "
        f"({result.cost.status.value})"
    )
    print(f"latency_ms        {result.latency_ms}")
    print(f"success           {result.success}")
    if result.warnings:
        print(f"warnings          {result.warnings}")
    if result.error is not None:
        print(f"error             {result.error.code.value}: {result.error.message}")
    if result.output is not None:
        print("output            " + json.dumps(result.output.model_dump(mode="json")))


# --- inspection commands -----------------------------------------------------------
def cmd_list_providers(_: argparse.Namespace) -> int:
    settings = get_settings()
    providers = build_providers(settings)
    print(f"default provider  {settings.llm_default_provider}")
    print(f"fallback order    {settings.llm_fallback_order}")
    for name, provider in sorted(providers.items()):
        caps = ", ".join(sorted(c.value for c in provider.capabilities.capabilities))
        print(
            f"- {name:8} available={provider.is_available()!s:5} "
            f"model={provider.default_model} caps=[{caps}]"
        )
    return 0


def cmd_list_tasks(_: argparse.Namespace) -> int:
    for task_type, definition in TASK_DEFINITIONS.items():
        print(f"- {task_type.value}")
        print(f"    purpose      {definition.purpose}")
        print(f"    prompt       {definition.prompt_name}")
        print(f"    output       {definition.output_schema_name}")
        if definition.allowed_tools:
            print(f"    tools        {', '.join(definition.allowed_tools)}")
        print(
            "    limits       "
            f"in={definition.max_input_chars} out={definition.max_output_tokens} "
            f"timeout={definition.timeout_seconds}s"
        )
    return 0


def cmd_list_prompts(_: argparse.Namespace) -> int:
    for definition in get_prompt_registry().all_definitions():
        print(
            f"- {definition.name}@{definition.semantic_version} "
            f"[{definition.status.value}] task={definition.task_type.value} "
            f"hash={definition.template_hash[:12]}"
        )
    return 0


def cmd_show_prompt(args: argparse.Namespace) -> int:
    registry = get_prompt_registry()
    definition = registry.get(args.name, args.version)
    print(f"name              {definition.name}")
    print(f"version           {definition.semantic_version}")
    print(f"status            {definition.status.value}")
    print(f"task              {definition.task_type.value}")
    print(f"hash              {definition.template_hash}")
    print(f"required vars     {', '.join(definition.required_context_fields)}")
    print(f"allowed tools     {', '.join(definition.allowed_tools) or '(none)'}")
    print(f"security notes    {definition.security_notes}")
    print("--- system template ---")
    print(definition.system_template)
    print("--- user template ---")
    print(definition.user_template)
    return 0


# --- DB-aware task commands --------------------------------------------------------
async def _ticket_context(reference: str) -> dict[str, Any]:
    sm = get_sessionmaker()
    async with sm() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.ticket_reference == reference)
        )
        if ticket is None:
            raise SystemExit(f"ticket {reference!r} not found")
        first_msg = await session.scalar(
            select(TicketMessage)
            .where(
                TicketMessage.ticket_id == ticket.id,
                TicketMessage.sender == MessageSender.customer,
            )
            .order_by(TicketMessage.sequence_number)
            .limit(1)
        )
        email: str | None = None
        if ticket.customer_id is not None:
            customer = await session.get(Customer, ticket.customer_id)
            email = customer.email if customer else None
        return {
            "ticket_id": ticket.id,
            "subject": ticket.subject,
            "message": first_msg.body if first_msg else ticket.subject,
            "category": ticket.category.value,
            "injection_flag": ticket.injection_flag,
            "email": email,
        }


def _run(request: ModelTaskRequest) -> int:
    service = ModelService()
    result = asyncio.run(service.run_task(request))
    _print_result(result)
    return 0 if result.success else 1


def cmd_classify_ticket(args: argparse.Namespace) -> int:
    ctx = asyncio.run(_ticket_context(args.ticket))
    request = builders.build_classification_request(
        subject=ctx["subject"],
        message=ctx["message"],
        injection_flag=ctx["injection_flag"],
        ticket_id=ctx["ticket_id"],
    )
    return _run(request)


def cmd_extract_identifiers(args: argparse.Namespace) -> int:
    ctx = asyncio.run(_ticket_context(args.ticket))
    request = builders.build_identifier_request(
        message=ctx["message"], ticket_id=ctx["ticket_id"]
    )
    return _run(request)


def cmd_plan_tools(args: argparse.Namespace) -> int:
    ctx = asyncio.run(_ticket_context(args.ticket))
    request = builders.build_tool_planning_request(
        category=ctx["category"],
        message=ctx["message"],
        known_email=ctx["email"],
        ticket_id=ctx["ticket_id"],
    )
    return _run(request)


# --- fixture-based commands --------------------------------------------------------
def cmd_summarise_evidence(args: argparse.Namespace) -> int:
    fixture = get_demo_fixture(args.fixture)
    request = builders.build_evidence_summary_request(
        topic=fixture.topic,
        citations=list(fixture.citations),
        excerpts=fixture.excerpts,
        support_status=fixture.support_status,
        conflict_status=fixture.conflict_status,
        rule_result=fixture.rule_result,
    )
    return _run(request)


def cmd_draft_response(args: argparse.Namespace) -> int:
    fixture = get_demo_fixture(args.fixture)
    request = builders.build_response_drafting_request(
        customer_name=fixture.customer_name,
        category=fixture.category,
        message=fixture.message,
        rule_result=fixture.rule_result,
        allowed_actions=fixture.allowed_actions,
        approval_required=fixture.approval_required,
        requires_more_information=fixture.requires_more_information,
        citations=list(fixture.citations),
        excerpts=fixture.excerpts,
    )
    return _run(request)


def cmd_run_demo(args: argparse.Namespace) -> int:
    fixture = get_demo_fixture(args.fixture)
    print(f"=== demo {fixture.id}: {fixture.description} ===")
    print("\n[1] classification")
    cmd_classify_from_fixture(fixture)
    print("\n[2] response drafting")
    cmd_draft_response(argparse.Namespace(fixture=fixture.id))
    return 0


def cmd_classify_from_fixture(fixture: Any) -> None:
    request = builders.build_classification_request(
        subject=fixture.subject,
        message=fixture.message,
        injection_flag=fixture.injection_flag,
    )
    service = ModelService()
    _print_result(asyncio.run(service.run_task(request)))


def cmd_stats(_: argparse.Namespace) -> int:
    async def _go() -> None:
        sm = get_sessionmaker()
        async with sm() as session:
            total = await session.scalar(select(func.count()).select_from(ModelCall))
            print(f"model_calls total    {total}")
            rows = await session.execute(
                select(ModelCall.task_type, func.count())
                .group_by(ModelCall.task_type)
                .order_by(ModelCall.task_type)
            )
            for task_type, count in rows:
                print(f"  {task_type.value:26} {count}")
            providers = await session.execute(
                select(ModelCall.provider, func.count()).group_by(ModelCall.provider)
            )
            for provider, count in providers:
                print(f"  provider={provider:10} {count}")

    asyncio.run(_go())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.llm.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-providers").set_defaults(func=cmd_list_providers)
    sub.add_parser("list-tasks").set_defaults(func=cmd_list_tasks)
    sub.add_parser("list-prompts").set_defaults(func=cmd_list_prompts)

    show = sub.add_parser("show-prompt")
    show.add_argument("name")
    show.add_argument("--version", default="1.0.0")
    show.set_defaults(func=cmd_show_prompt)

    for name, handler in (
        ("classify-ticket", cmd_classify_ticket),
        ("extract-identifiers", cmd_extract_identifiers),
        ("plan-tools", cmd_plan_tools),
    ):
        p = sub.add_parser(name)
        p.add_argument("ticket")
        p.set_defaults(func=handler)

    for name, handler in (
        ("summarise-evidence", cmd_summarise_evidence),
        ("draft-response", cmd_draft_response),
    ):
        p = sub.add_parser(name)
        p.add_argument("--fixture", required=True, choices=sorted(DEMO_FIXTURES))
        p.set_defaults(func=handler)

    demo = sub.add_parser("run-demo")
    demo.add_argument("fixture", choices=sorted(DEMO_FIXTURES))
    demo.set_defaults(func=cmd_run_demo)

    sub.add_parser("stats").set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
