"""Deterministic synthetic-data generator for Meridian & Co.

Everything is driven by a single fixed seed and a fixed ``REFERENCE_DATE`` so the
dataset (business fields, references, totals and boundary dates) is fully reproducible.
Password hashes and wall-clock ``created_at`` defaults are the only non-reproducible
bits and are excluded from determinism checks.

No LLMs, network or external services are used.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from faker import Faker

from app.models.customer import Customer
from app.models.enums import (
    CustomerTier,
    MessageSender,
    OrderStatus,
    ShipmentStatus,
    TicketCategory,
    TicketPriority,
    TicketStatus,
    UserRole,
)
from app.models.order import Order, OrderItem
from app.models.policy import Policy, PolicyVersion
from app.models.product import Product
from app.models.shipment import Shipment
from app.models.ticket import Ticket, TicketMessage
from app.models.user import User
from app.seeds.catalogue import PRODUCTS, ProductSpec
from app.seeds.policies import build_policy_defs
from app.seeds.security import hash_dev_password
from app.seeds.tickets_content import (
    ADVERSARIAL,
    CATEGORY_TEMPLATES,
    DEMO_MESSAGES,
)

SEED = 20260716
REFERENCE_DATE = date(2026, 7, 16)
REFERENCE_DT = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)

CARRIERS = ("Meridian Express", "Parcel Nimbus", "RoyalPost UK", "SwiftHaul")

_ORDER_STATUS_WEIGHTS: dict[OrderStatus, float] = {
    OrderStatus.delivered: 0.42,
    OrderStatus.shipped: 0.12,
    OrderStatus.processing: 0.08,
    OrderStatus.paid: 0.06,
    OrderStatus.placed: 0.05,
    OrderStatus.cancelled: 0.08,
    OrderStatus.partially_refunded: 0.07,
    OrderStatus.refunded: 0.12,
}

NUM_CUSTOMERS = 55
NUM_ORDINARY_ORDERS = 150
NUM_ORDINARY_TICKETS = 66


@dataclass(slots=True)
class Dataset:
    users: list[User] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    tickets: list[Ticket] = field(default_factory=list)

    def all_objects(self) -> list[object]:
        # Order matters for FK dependencies during flush.
        return [
            *self.users,
            *self.products,
            *self.customers,
            *self.policies,
            *self.orders,
            *self.tickets,
        ]


@dataclass(slots=True)
class ShipmentPlan:
    status: ShipmentStatus
    promised_delivery_date: date
    shipped_at: datetime | None
    delivered_at: datetime | None


class SyntheticGenerator:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.faker = Faker("en_GB")
        self.faker.seed_instance(SEED)
        self._order_seq = 0
        self._ticket_seq = 0
        self._customer_seq = 0
        self._tracking_seq = 0

    # --- id / reference helpers ---------------------------------------------
    def _uuid(self) -> uuid.UUID:
        return uuid.UUID(int=self.rng.getrandbits(128), version=4)

    def _next_order_number(self) -> str:
        self._order_seq += 1
        return f"MER-2026-{self._order_seq:06d}"

    def _next_ticket_reference(self) -> str:
        self._ticket_seq += 1
        return f"TKT-2026-{self._ticket_seq:06d}"

    def _next_customer_reference(self) -> str:
        self._customer_seq += 1
        return f"MER-C-{self._customer_seq:05d}"

    def _next_tracking(self) -> str:
        self._tracking_seq += 1
        return f"MER-TRK-{self._tracking_seq:08d}"

    def _uk_phone(self) -> str:
        return f"07{self.rng.randint(100000000, 999999999)}"

    # --- entity builders -----------------------------------------------------
    def _build_users(self) -> list[User]:
        specs = [
            ("agent.amara@meridian.example", "Amara Okafor", UserRole.support_agent),
            ("agent.tom@meridian.example", "Tom Fletcher", UserRole.support_agent),
            ("super.priya@meridian.example", "Priya Nair", UserRole.supervisor),
            ("super.george@meridian.example", "George Adeyemi", UserRole.supervisor),
        ]
        return [
            User(
                id=self._uuid(),
                email=email,
                display_name=name,
                role=role,
                hashed_password=hash_dev_password(),
                is_active=True,
            )
            for email, name, role in specs
        ]

    def _build_products(self) -> list[Product]:
        products: list[Product] = []
        for spec in PRODUCTS:
            products.append(self._product_from_spec(spec))
        return products

    def _product_from_spec(self, spec: ProductSpec) -> Product:
        return Product(
            id=self._uuid(),
            sku=spec.sku,
            name=spec.name,
            description=spec.description,
            category=spec.category,
            unit_price_pence=spec.unit_price_pence,
            is_active=spec.is_active,
        )

    def _build_customers(self) -> list[Customer]:
        customers: list[Customer] = []
        seen_emails: set[str] = set()
        for _ in range(NUM_CUSTOMERS):
            first = self.faker.first_name()
            last = self.faker.last_name()
            base = f"{first}.{last}".lower().replace(" ", "").replace("'", "")
            email = f"{base}@example.com"
            suffix = 1
            while email in seen_emails:
                suffix += 1
                email = f"{base}{suffix}@example.com"
            seen_emails.add(email)
            tier = (
                CustomerTier.vip if self.rng.random() < 0.2 else CustomerTier.standard
            )
            customers.append(
                Customer(
                    id=self._uuid(),
                    external_reference=self._next_customer_reference(),
                    first_name=first,
                    last_name=last,
                    email=email,
                    phone=self._uk_phone(),
                    tier=tier,
                )
            )
        return customers

    def _build_policies(self) -> list[Policy]:
        policies: list[Policy] = []
        for pdef in build_policy_defs(REFERENCE_DATE):
            policy = Policy(
                id=self._uuid(),
                topic=pdef.topic,
                title=pdef.title,
                description=pdef.description,
            )
            policy.versions = [
                PolicyVersion(
                    id=self._uuid(),
                    version=v.version,
                    status=v.status,
                    body=v.body,
                    effective_from=v.effective_from,
                    effective_to=v.effective_to,
                )
                for v in pdef.versions
            ]
            policies.append(policy)
        return policies

    # --- orders --------------------------------------------------------------
    def _pick_items(self, active_products: list[Product], count: int) -> list[Product]:
        return self.rng.sample(active_products, k=count)

    def _shipment_plan_for(
        self, status: OrderStatus, placed_at: datetime
    ) -> ShipmentPlan | None:
        promised = (placed_at + timedelta(days=self.rng.randint(3, 6))).date()
        if status in (OrderStatus.placed, OrderStatus.paid, OrderStatus.cancelled):
            return None
        if status == OrderStatus.processing:
            return ShipmentPlan(ShipmentStatus.label_created, promised, None, None)
        if status == OrderStatus.shipped:
            shipped = placed_at + timedelta(days=self.rng.randint(1, 2))
            ship_status = self.rng.choice(
                [ShipmentStatus.in_transit, ShipmentStatus.out_for_delivery]
            )
            return ShipmentPlan(ship_status, promised, shipped, None)
        # delivered / partially_refunded / refunded -> delivered on time
        shipped = placed_at + timedelta(days=self.rng.randint(1, 2))
        delivered = shipped + timedelta(days=self.rng.randint(1, 3))
        return ShipmentPlan(ShipmentStatus.delivered, promised, shipped, delivered)

    def _make_order(
        self,
        customer: Customer,
        products: list[Product],
        quantities: list[int],
        status: OrderStatus,
        placed_at: datetime,
        shipment_plan: ShipmentPlan | None,
        *,
        force_discount: bool = False,
    ) -> Order:
        items: list[OrderItem] = []
        subtotal = 0
        for product, qty in zip(products, quantities, strict=True):
            line_total = qty * product.unit_price_pence
            subtotal += line_total
            items.append(
                OrderItem(
                    id=self._uuid(),
                    product_id=product.id,
                    quantity=qty,
                    unit_price_pence=product.unit_price_pence,
                    line_total_pence=line_total,
                )
            )

        delivery_fee = 0 if subtotal >= 4000 else self.rng.choice([395, 495])
        discount = 0
        if force_discount or self.rng.random() < 0.25:
            rate = self.rng.choice([0.10, 0.15, 0.20])
            discount = min(int(round(subtotal * rate)), subtotal)
        total_paid = subtotal + delivery_fee - discount

        order = Order(
            id=self._uuid(),
            order_number=self._next_order_number(),
            customer_id=customer.id,
            status=status,
            subtotal_pence=subtotal,
            delivery_fee_pence=delivery_fee,
            discount_pence=discount,
            total_paid_pence=total_paid,
            placed_at=placed_at,
        )
        order.items = items
        if status in (OrderStatus.partially_refunded, OrderStatus.refunded):
            for item in items:
                item.is_returned = True
                item.returned_at = placed_at + timedelta(days=self.rng.randint(5, 20))

        if shipment_plan is not None:
            order.shipment = Shipment(
                id=self._uuid(),
                carrier=self.rng.choice(CARRIERS),
                tracking_number=self._next_tracking(),
                status=shipment_plan.status,
                promised_delivery_date=shipment_plan.promised_delivery_date,
                shipped_at=shipment_plan.shipped_at,
                delivered_at=shipment_plan.delivered_at,
                last_updated_at=placed_at + timedelta(days=self.rng.randint(1, 5)),
            )
        return order

    def _build_ordinary_orders(
        self, customers: list[Customer], active_products: list[Product]
    ) -> list[Order]:
        statuses = list(_ORDER_STATUS_WEIGHTS.keys())
        weights = list(_ORDER_STATUS_WEIGHTS.values())
        orders: list[Order] = []
        for _ in range(NUM_ORDINARY_ORDERS):
            customer = self.rng.choice(customers)
            item_count = self.rng.randint(1, 5)
            products = self._pick_items(active_products, item_count)
            quantities = [self.rng.randint(1, 3) for _ in products]
            status = self.rng.choices(statuses, weights=weights, k=1)[0]
            placed_at = REFERENCE_DT - timedelta(
                days=self.rng.randint(2, 150), hours=self.rng.randint(0, 23)
            )
            plan = self._shipment_plan_for(status, placed_at)
            orders.append(
                self._make_order(
                    customer, products, quantities, status, placed_at, plan
                )
            )
        return orders

    def _build_scenario_orders(
        self,
        customers: list[Customer],
        active_products: list[Product],
        product_by_sku: dict[str, Product],
    ) -> dict[str, Order]:
        """Build the fixed boundary/demo orders keyed by scenario name."""
        scenarios: dict[str, Order] = {}
        lamp = product_by_sku["MER-DEC-001"]  # Terracotta Table Lamp, £59

        def single(product: Product, qty: int = 1) -> tuple[list[Product], list[int]]:
            return [product], [qty]

        # In-transit tracking demo.
        placed = REFERENCE_DT - timedelta(days=4)
        prods, qtys = single(active_products[0])
        scenarios["tracking"] = self._make_order(
            customers[0],
            prods,
            qtys,
            OrderStatus.shipped,
            placed,
            ShipmentPlan(
                ShipmentStatus.in_transit,
                (placed + timedelta(days=3)).date(),
                placed + timedelta(days=1),
                None,
            ),
        )

        # Refund-approval demo: delivered lamp (>£50).
        placed = REFERENCE_DT - timedelta(days=10)
        scenarios["refund_approval"] = self._make_order(
            customers[1],
            *single(lamp, 1),
            OrderStatus.delivered,
            placed,
            ShipmentPlan(
                ShipmentStatus.delivered,
                (placed + timedelta(days=4)).date(),
                placed + timedelta(days=1),
                placed + timedelta(days=3),
            ),
        )

        # Duplicate-refund demo: already refunded order.
        placed = REFERENCE_DT - timedelta(days=25)
        scenarios["duplicate_refund"] = self._make_order(
            customers[2],
            *single(active_products[3], 2),
            OrderStatus.refunded,
            placed,
            ShipmentPlan(
                ShipmentStatus.delivered,
                (placed + timedelta(days=4)).date(),
                placed + timedelta(days=1),
                placed + timedelta(days=3),
            ),
        )

        # Return day-30 boundary: delivered exactly 30 days ago.
        placed = REFERENCE_DT - timedelta(days=34)
        delivered = REFERENCE_DT - timedelta(days=30)
        scenarios["return_day_30"] = self._make_order(
            customers[3],
            *single(active_products[5], 1),
            OrderStatus.delivered,
            placed,
            ShipmentPlan(
                ShipmentStatus.delivered,
                delivered.date(),
                placed + timedelta(days=1),
                delivered,
            ),
        )

        # Return day-31 boundary: delivered 31 days ago (outside window).
        placed = REFERENCE_DT - timedelta(days=35)
        delivered = REFERENCE_DT - timedelta(days=31)
        scenarios["return_day_31"] = self._make_order(
            customers[4],
            *single(active_products[6], 1),
            OrderStatus.delivered,
            placed,
            ShipmentPlan(
                ShipmentStatus.delivered,
                delivered.date(),
                placed + timedelta(days=1),
                delivered,
            ),
        )

        # Delivered more than 30 days ago (45).
        placed = REFERENCE_DT - timedelta(days=49)
        delivered = REFERENCE_DT - timedelta(days=45)
        scenarios["delivered_45"] = self._make_order(
            customers[5],
            *single(active_products[7], 1),
            OrderStatus.delivered,
            placed,
            ShipmentPlan(
                ShipmentStatus.delivered,
                delivered.date(),
                placed + timedelta(days=1),
                delivered,
            ),
        )

        # Delivered late (after promised date).
        placed = REFERENCE_DT - timedelta(days=12)
        scenarios["delivered_late"] = self._make_order(
            customers[6],
            *single(active_products[8], 1),
            OrderStatus.delivered,
            placed,
            ShipmentPlan(
                ShipmentStatus.delivered,
                (placed + timedelta(days=3)).date(),
                placed + timedelta(days=1),
                placed + timedelta(days=7),
            ),
        )

        # Promised date already missed, still in transit.
        placed = REFERENCE_DT - timedelta(days=12)
        scenarios["promised_missed"] = self._make_order(
            customers[7],
            *single(active_products[9], 1),
            OrderStatus.shipped,
            placed,
            ShipmentPlan(
                ShipmentStatus.in_transit,
                (REFERENCE_DT - timedelta(days=5)).date(),
                placed + timedelta(days=1),
                None,
            ),
        )

        # Exception.
        placed = REFERENCE_DT - timedelta(days=8)
        scenarios["exception"] = self._make_order(
            customers[8],
            *single(active_products[10], 1),
            OrderStatus.shipped,
            placed,
            ShipmentPlan(
                ShipmentStatus.exception,
                (placed + timedelta(days=4)).date(),
                placed + timedelta(days=1),
                None,
            ),
        )

        # Lost.
        placed = REFERENCE_DT - timedelta(days=20)
        scenarios["lost"] = self._make_order(
            customers[9],
            *single(active_products[11], 1),
            OrderStatus.shipped,
            placed,
            ShipmentPlan(
                ShipmentStatus.lost,
                (placed + timedelta(days=4)).date(),
                placed + timedelta(days=1),
                None,
            ),
        )

        # Out for delivery (guarantee coverage).
        placed = REFERENCE_DT - timedelta(days=3)
        scenarios["out_for_delivery"] = self._make_order(
            customers[10],
            *single(active_products[12], 1),
            OrderStatus.shipped,
            placed,
            ShipmentPlan(
                ShipmentStatus.out_for_delivery,
                (placed + timedelta(days=2)).date(),
                placed + timedelta(days=1),
                None,
            ),
        )
        return scenarios

    # --- tickets -------------------------------------------------------------
    def _customer_first_name(self, customer: Customer) -> str:
        return customer.first_name

    def _make_ticket(
        self,
        category: TicketCategory,
        subject: str,
        body: str,
        *,
        customer: Customer | None,
        order: Order | None,
        received_at: datetime,
        status: TicketStatus,
        injection_flag: bool = False,
        priority: TicketPriority = TicketPriority.normal,
        confidence: float | None = None,
        seed_tag: str | None = None,
        trusted_sender: MessageSender = MessageSender.customer,
    ) -> Ticket:
        ticket = Ticket(
            id=self._uuid(),
            ticket_reference=self._next_ticket_reference(),
            customer_id=customer.id if customer else None,
            order_id=order.id if order else None,
            category=category,
            status=status,
            subject=subject,
            classification_confidence=confidence,
            injection_flag=injection_flag,
            priority=priority,
            received_at=received_at,
            resolved_at=received_at + timedelta(hours=6)
            if status in (TicketStatus.resolved, TicketStatus.closed)
            else None,
            seed_tag=seed_tag,
        )
        ticket.messages = [
            TicketMessage(
                id=self._uuid(),
                sender=MessageSender.customer,
                body=body,
                is_trusted=False,
                sequence_number=1,
            )
        ]
        return ticket

    def _build_ordinary_tickets(
        self,
        customers: list[Customer],
        orders_by_customer: dict[uuid.UUID, list[Order]],
    ) -> list[Ticket]:
        customers_with_orders = [c for c in customers if orders_by_customer.get(c.id)]
        tickets: list[Ticket] = []
        categories = list(TicketCategory)
        # Guarantee at least one ticket per category, then fill the remainder.
        plan = list(categories) + [
            self.rng.choice(categories)
            for _ in range(NUM_ORDINARY_TICKETS - len(categories))
        ]
        ticket_statuses = [
            TicketStatus.received,
            TicketStatus.awaiting_agent,
            TicketStatus.needs_information,
            TicketStatus.resolved,
            TicketStatus.closed,
            TicketStatus.escalated,
        ]
        for category in plan:
            template = self.rng.choice(CATEGORY_TEMPLATES[category])
            order: Order | None = None
            if template.needs_order and customers_with_orders:
                customer = self.rng.choice(customers_with_orders)
                missing = self.rng.random() < 0.15
                if not missing:
                    order = self.rng.choice(orders_by_customer[customer.id])
            else:
                customer = self.rng.choice(customers)
            order_ref = order.order_number if order else "my recent order"
            body = template.body.format(order=order_ref, name=customer.first_name)
            received_at = REFERENCE_DT - timedelta(
                days=self.rng.randint(0, 60), hours=self.rng.randint(0, 23)
            )
            status = self.rng.choice(ticket_statuses)
            confidence = (
                round(self.rng.uniform(0.55, 0.98), 2)
                if self.rng.random() < 0.6
                else None
            )
            tickets.append(
                self._make_ticket(
                    category,
                    template.subject,
                    body,
                    customer=customer,
                    order=order,
                    received_at=received_at,
                    status=status,
                    confidence=confidence,
                )
            )
        return tickets

    def _build_adversarial_tickets(self, customers: list[Customer]) -> list[Ticket]:
        tickets: list[Ticket] = []
        for adv in ADVERSARIAL:
            customer = self.rng.choice(customers)
            received_at = REFERENCE_DT - timedelta(
                days=self.rng.randint(0, 30), hours=self.rng.randint(0, 23)
            )
            tickets.append(
                self._make_ticket(
                    adv.category,
                    adv.subject,
                    adv.body,
                    customer=customer,
                    order=None,  # adversarial tickets never link a real order
                    received_at=received_at,
                    status=TicketStatus.escalated,
                    injection_flag=True,
                    priority=TicketPriority.high,
                    seed_tag=adv.tag,
                )
            )
        return tickets

    def _build_demo_tickets(
        self, customers: list[Customer], scenarios: dict[str, Order]
    ) -> list[Ticket]:
        tickets: list[Ticket] = []

        def demo(
            tag: str,
            category: TicketCategory,
            subject: str,
            scenario_key: str,
            *,
            owner: Customer,
            link_order: bool,
            injection: bool = False,
            order_number_override: str | None = None,
        ) -> None:
            order = scenarios[scenario_key]
            order_ref = order_number_override or order.order_number
            body = DEMO_MESSAGES[tag].format(order=order_ref)
            tickets.append(
                self._make_ticket(
                    category,
                    subject,
                    body,
                    customer=owner,
                    order=order if link_order else None,
                    received_at=REFERENCE_DT - timedelta(days=1),
                    status=TicketStatus.received,
                    injection_flag=injection,
                    priority=TicketPriority.high
                    if injection
                    else TicketPriority.normal,
                    seed_tag=tag,
                )
            )

        # Owners chosen to match the customer each scenario order belongs to.
        by_id = {c.id: c for c in customers}
        demo(
            "DEMO-TRACKING-001",
            TicketCategory.order_tracking,
            "Where is my order?",
            "tracking",
            owner=by_id[scenarios["tracking"].customer_id],
            link_order=True,
        )
        demo(
            "DEMO-REFUND-APPROVAL-001",
            TicketCategory.refund_request,
            "Refund for damaged lamp",
            "refund_approval",
            owner=by_id[scenarios["refund_approval"].customer_id],
            link_order=True,
        )
        demo(
            "DEMO-DUPLICATE-REFUND-001",
            TicketCategory.refund_request,
            "Refund not received",
            "duplicate_refund",
            owner=by_id[scenarios["duplicate_refund"].customer_id],
            link_order=True,
        )
        demo(
            "DEMO-RETURN-DAY-30",
            TicketCategory.return_request,
            "Return request",
            "return_day_30",
            owner=by_id[scenarios["return_day_30"].customer_id],
            link_order=True,
        )
        demo(
            "DEMO-RETURN-DAY-31",
            TicketCategory.return_request,
            "Return request",
            "return_day_31",
            owner=by_id[scenarios["return_day_31"].customer_id],
            link_order=True,
        )
        # Prompt-injection demo: not linked to an order.
        demo(
            "DEMO-PROMPT-INJECTION-001",
            TicketCategory.refund_request,
            "Refund",
            "refund_approval",
            owner=by_id[scenarios["refund_approval"].customer_id],
            link_order=False,
            injection=True,
        )
        # Cross-customer demo: a different customer references someone else's order in
        # the message body, but no order is linked (DB ownership stays valid).
        target_order = scenarios["duplicate_refund"]
        attacker = next(c for c in customers if c.id != target_order.customer_id)
        demo(
            "DEMO-CROSS-CUSTOMER-001",
            TicketCategory.order_tracking,
            "Order lookup",
            "duplicate_refund",
            owner=attacker,
            link_order=False,
            order_number_override=target_order.order_number,
        )
        return tickets

    # --- top-level build -----------------------------------------------------
    def build(self) -> Dataset:
        dataset = Dataset()
        dataset.users = self._build_users()
        dataset.products = self._build_products()
        dataset.customers = self._build_customers()
        dataset.policies = self._build_policies()

        active_products = [p for p in dataset.products if p.is_active]
        product_by_sku = {p.sku: p for p in dataset.products}

        ordinary_orders = self._build_ordinary_orders(
            dataset.customers, active_products
        )
        scenarios = self._build_scenario_orders(
            dataset.customers, active_products, product_by_sku
        )
        dataset.orders = ordinary_orders + list(scenarios.values())

        orders_by_customer: dict[uuid.UUID, list[Order]] = {}
        for order in dataset.orders:
            orders_by_customer.setdefault(order.customer_id, []).append(order)

        dataset.tickets = (
            self._build_ordinary_tickets(dataset.customers, orders_by_customer)
            + self._build_adversarial_tickets(dataset.customers)
            + self._build_demo_tickets(dataset.customers, scenarios)
        )
        return dataset


def build_dataset() -> Dataset:
    """Build the full deterministic dataset in memory."""
    return SyntheticGenerator().build()
