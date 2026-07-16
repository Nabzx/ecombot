"""Hand-authored Meridian & Co. product catalogue.

Realistic UK homeware products across the five categories. Prices are in integer
pennies (GBP). A small number are marked inactive. This list is fixed so the seed is
deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ProductCategory


@dataclass(frozen=True, slots=True)
class ProductSpec:
    sku: str
    name: str
    description: str
    category: ProductCategory
    unit_price_pence: int
    is_active: bool = True


K = ProductCategory.kitchenware
D = ProductCategory.home_decor
B = ProductCategory.bedding_and_towels
F = ProductCategory.small_furniture
A = ProductCategory.consumer_accessories

PRODUCTS: tuple[ProductSpec, ...] = (
    # Kitchenware
    ProductSpec(
        "MER-KIT-001",
        "Stoneware Dinner Set (12pc)",
        "Reactive-glaze stoneware dinner set for four.",
        K,
        6400,
    ),
    ProductSpec(
        "MER-KIT-002",
        "Cast Iron Casserole 4L",
        "Enamelled cast iron casserole in sage green.",
        K,
        7900,
    ),
    ProductSpec(
        "MER-KIT-003",
        "Acacia Chopping Board",
        "End-grain acacia board with juice groove.",
        K,
        2450,
    ),
    ProductSpec(
        "MER-KIT-004",
        "Copper Whisk Set",
        "Three stainless whisks with copper handles.",
        K,
        1850,
    ),
    ProductSpec(
        "MER-KIT-005",
        "Glass Storage Jars (Set of 5)",
        "Airtight borosilicate jars with bamboo lids.",
        K,
        3200,
    ),
    ProductSpec(
        "MER-KIT-006",
        "Ceramic Utensil Crock",
        "Matte ceramic crock for worktop utensils.",
        K,
        1990,
    ),
    ProductSpec(
        "MER-KIT-007",
        "Espresso Moka Pot 6-cup",
        "Aluminium stovetop moka pot.",
        K,
        2800,
    ),
    ProductSpec(
        "MER-KIT-008",
        "Non-Stick Frying Pan 28cm",
        "Forged aluminium pan, induction safe.",
        K,
        3600,
    ),
    ProductSpec(
        "MER-KIT-009",
        "Marble Pestle & Mortar",
        "Solid marble mortar with pouring lip.",
        K,
        2650,
    ),
    ProductSpec(
        "MER-KIT-010",
        "Linen Tea Towel Trio",
        "Stonewashed linen tea towels, striped.",
        K,
        1450,
    ),
    # Home decor
    ProductSpec(
        "MER-DEC-001",
        "Terracotta Table Lamp",
        "Ribbed terracotta base with linen shade.",
        D,
        5900,
    ),
    ProductSpec(
        "MER-DEC-002",
        "Framed Botanical Print A2",
        "Giclee botanical print in oak frame.",
        D,
        3400,
    ),
    ProductSpec(
        "MER-DEC-003",
        "Handwoven Jute Rug 120x170",
        "Natural jute flatweave rug.",
        D,
        8900,
    ),
    ProductSpec(
        "MER-DEC-004",
        "Scented Soy Candle - Fig",
        "40-hour soy candle in amber glass.",
        D,
        1650,
    ),
    ProductSpec(
        "MER-DEC-005",
        "Ceramic Vase Trio",
        "Set of three matte stoneware bud vases.",
        D,
        2900,
    ),
    ProductSpec(
        "MER-DEC-006",
        "Wool Throw Blanket",
        "Lambswool-blend throw with fringe.",
        D,
        5500,
    ),
    ProductSpec(
        "MER-DEC-007",
        "Rattan Wall Mirror 60cm",
        "Round mirror with woven rattan frame.",
        D,
        4700,
    ),
    ProductSpec(
        "MER-DEC-008", "Brass Photo Frame 6x4", "Brushed brass tabletop frame.", D, 1550
    ),
    ProductSpec(
        "MER-DEC-009",
        "Velvet Cushion Cover 45cm",
        "Piped velvet cover in ochre.",
        D,
        1800,
    ),
    ProductSpec(
        "MER-DEC-010",
        "Stoneware Plant Pot 18cm",
        "Speckled indoor planter with saucer.",
        D,
        2200,
    ),
    # Bedding and towels
    ProductSpec(
        "MER-BED-001",
        "Egyptian Cotton Duvet Set King",
        "300TC duvet cover set, white.",
        B,
        6900,
    ),
    ProductSpec(
        "MER-BED-002",
        "Linen Pillowcases (Pair)",
        "Stonewashed French linen pillowcases.",
        B,
        3200,
    ),
    ProductSpec(
        "MER-BED-003",
        "Turkish Bath Towel Bundle",
        "Two bath sheets, two hand towels.",
        B,
        4200,
    ),
    ProductSpec(
        "MER-BED-004",
        "Brushed Cotton Fitted Sheet Double",
        "Soft brushed cotton, deep fitted.",
        B,
        2600,
    ),
    ProductSpec(
        "MER-BED-005",
        "Waffle Weave Bath Robe",
        "Lightweight cotton waffle robe.",
        B,
        3900,
    ),
    ProductSpec(
        "MER-BED-006", "Duck Down Pillow Pair", "Medium-support down pillows.", B, 4800
    ),
    ProductSpec(
        "MER-BED-007",
        "Merino Wool Blanket",
        "Reversible merino blanket, charcoal.",
        B,
        7400,
    ),
    ProductSpec(
        "MER-BED-008",
        "Organic Cotton Hand Towels x4",
        "GOTS-certified hand towels.",
        B,
        2400,
    ),
    # Small furniture
    ProductSpec(
        "MER-FUR-001",
        "Oak Nesting Tables (Set of 2)",
        "Solid oak nesting side tables.",
        F,
        12900,
    ),
    ProductSpec(
        "MER-FUR-002",
        "Rattan Storage Ottoman",
        "Upholstered lid ottoman with storage.",
        F,
        8600,
    ),
    ProductSpec(
        "MER-FUR-003",
        "Bamboo Bathroom Shelf",
        "Three-tier freestanding bamboo shelf.",
        F,
        4500,
    ),
    ProductSpec(
        "MER-FUR-004",
        "Velvet Accent Stool",
        "Round footstool with wooden legs.",
        F,
        5400,
    ),
    ProductSpec(
        "MER-FUR-005",
        "Wall-Mounted Coat Rack",
        "Oak rail with five brass hooks.",
        F,
        3300,
    ),
    ProductSpec(
        "MER-FUR-006",
        "Folding Bistro Chair",
        "Powder-coated steel folding chair.",
        F,
        3900,
    ),
    # Consumer accessories
    ProductSpec(
        "MER-ACC-001",
        "Leather Coaster Set",
        "Four vegetable-tanned leather coasters.",
        A,
        1600,
    ),
    ProductSpec(
        "MER-ACC-002",
        "Insulated Water Bottle 750ml",
        "Double-walled stainless bottle.",
        A,
        2100,
    ),
    ProductSpec(
        "MER-ACC-003",
        "Canvas Storage Baskets x2",
        "Collapsible cotton-canvas baskets.",
        A,
        1900,
    ),
    ProductSpec(
        "MER-ACC-004", "Reusable Beeswax Wraps", "Set of three assorted wraps.", A, 1200
    ),
    ProductSpec(
        "MER-ACC-005",
        "Bamboo Cutlery Travel Set",
        "Portable cutlery in cotton pouch.",
        A,
        1100,
    ),
    # Inactive / discontinued lines
    ProductSpec(
        "MER-DEC-011",
        "Retired Glass Lantern",
        "Discontinued hurricane lantern.",
        D,
        2700,
        is_active=False,
    ),
    ProductSpec(
        "MER-KIT-011",
        "Discontinued Melamine Set",
        "Withdrawn melamine picnic set.",
        K,
        1500,
        is_active=False,
    ),
    ProductSpec(
        "MER-ACC-006",
        "Legacy Cork Placemats",
        "Discontinued cork placemats.",
        A,
        900,
        is_active=False,
    ),
)
