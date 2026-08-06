"""The seeded two-level category taxonomy.

Slugs are hierarchical and permanent: `food_drink` for a parent,
`food_drink.groceries` for its child. Everything else in the system — the Plaid
mapping table, budget suggestions, the UI — references categories by slug, never
by name or id, so a category can be renamed without breaking anything.

`kind` drives money semantics rather than presentation:
  INCOME    — increases net worth, never budgeted against
  EXPENSE   — the only kind budgets apply to
  TRANSFER  — moves money between accounts the user already owns. Always
              excluded from spend so a credit-card payment or a brokerage
              contribution cannot masquerade as spending.
"""

from app.models.enums import CategoryKind

UNCATEGORIZED_SLUG = "uncategorized"

# (slug, name, kind, icon, [child names...])
TAXONOMY: list[tuple[str, str, CategoryKind, str, list[str]]] = [
    (
        "income", "Income", CategoryKind.INCOME, "banknote",
        [
            "Paycheck", "Bonus", "Self-Employment", "Interest", "Dividends",
            "Capital Gains", "Refunds & Reimbursements", "Government Benefits",
            "Rental Income", "Other Income",
        ],
    ),
    (
        "housing", "Housing", CategoryKind.EXPENSE, "house",
        [
            "Rent", "Mortgage", "Property Tax", "Home Insurance", "HOA Fees",
            "Home Maintenance", "Home Improvement", "Furniture",
            "Household Supplies",
        ],
    ),
    (
        "utilities", "Utilities", CategoryKind.EXPENSE, "plug",
        [
            "Electricity", "Gas & Heating", "Water & Sewer", "Trash & Recycling",
            "Internet", "Mobile Phone", "Cable & Satellite",
        ],
    ),
    (
        "food_drink", "Food & Drink", CategoryKind.EXPENSE, "utensils",
        [
            "Groceries", "Restaurants", "Fast Food", "Coffee Shops",
            "Bars & Alcohol", "Food Delivery",
        ],
    ),
    (
        "transportation", "Transportation", CategoryKind.EXPENSE, "car",
        [
            "Gas & Fuel", "Public Transit", "Rideshare & Taxi", "Parking",
            "Tolls", "Auto Insurance", "Auto Maintenance", "Auto Payment",
            "Auto Registration",
        ],
    ),
    (
        "shopping", "Shopping", CategoryKind.EXPENSE, "shopping-bag",
        [
            "Clothing", "Electronics", "Books", "Sporting Goods", "Hobbies",
            "Gifts", "Pet Supplies", "Online Marketplace", "Department Stores",
            "Office Supplies",
        ],
    ),
    (
        "health", "Health", CategoryKind.EXPENSE, "heart-pulse",
        [
            "Doctor", "Dentist", "Vision", "Pharmacy", "Health Insurance",
            "Mental Health", "Fitness & Gym", "Personal Care", "Veterinary",
        ],
    ),
    (
        "entertainment", "Entertainment", CategoryKind.EXPENSE, "clapperboard",
        [
            "Movies & TV", "Music", "Games", "Events & Concerts", "Sports",
            "Outdoors & Recreation",
        ],
    ),
    (
        "education", "Education", CategoryKind.EXPENSE, "graduation-cap",
        [
            "Tuition", "Student Loans", "Books & Supplies",
            "Courses & Training", "Childcare",
        ],
    ),
    (
        "travel", "Travel", CategoryKind.EXPENSE, "plane",
        ["Flights", "Hotels", "Car Rental", "Vacation", "Travel Insurance"],
    ),
    (
        "financial", "Financial", CategoryKind.EXPENSE, "landmark",
        [
            "Bank Fees", "ATM Fees", "Interest Charges", "Late Fees",
            "Foreign Transaction Fees", "Taxes", "Financial Advisory",
            "Insurance", "Charity & Donations", "Legal",
        ],
    ),
    (
        "subscriptions", "Subscriptions", CategoryKind.EXPENSE, "repeat",
        [
            "Streaming Services", "Software & Apps", "News & Magazines",
            "Memberships", "Cloud Storage",
        ],
    ),
    (
        "transfers", "Transfers", CategoryKind.TRANSFER, "arrow-left-right",
        [
            "Credit Card Payment", "Account Transfer", "Investment Contribution",
            "Retirement Contribution", "Savings Transfer", "Loan Payment",
            "Cash & ATM", "P2P Payment",
        ],
    ),
    (
        UNCATEGORIZED_SLUG, "Uncategorized", CategoryKind.EXPENSE, "circle-help", [],
    ),
]


def child_slug(parent_slug: str, child_name: str) -> str:
    """Derive a child's permanent slug from its display name."""
    token = (
        child_name.lower()
        .replace("&", "and")
        .replace("-", " ")
        .replace("/", " ")
    )
    token = "_".join(part for part in "".join(
        c if c.isalnum() or c.isspace() else " " for c in token
    ).split())
    return f"{parent_slug}.{token}"
