import enum


class AccountType(str, enum.Enum):
    depository = "depository"
    credit = "credit"
    loan = "loan"
    investment = "investment"
    other = "other"


class TransactionDirection(str, enum.Enum):
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"


class ItemStatus(str, enum.Enum):
    good = "good"
    login_required = "login_required"
    pending_expiration = "pending_expiration"
    error = "error"
    revoked = "revoked"


class CategoryKind(str, enum.Enum):
    """What a category does to net worth. Budgets only apply to EXPENSE."""

    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    TRANSFER = "TRANSFER"


class CategorySource(str, enum.Enum):
    """Which categorisation layer produced `transaction.category_id`.

    Ordered strongest to weakest. USER is sacred: nothing automated may ever
    overwrite a category a human chose by hand.
    """

    USER = "USER"  # manual pick in the ledger UI
    RULE = "RULE"  # layer 1 — user-defined categorisation_rule
    MERCHANT = "MERCHANT"  # layer 2 — merchant.default_category_id
    PROVIDER = "PROVIDER"  # layer 3 — Plaid PFC mapped into our taxonomy
    UNCATEGORIZED = "UNCATEGORIZED"  # layer 4 — holding pen


# Sources an automated re-categorisation pass is allowed to overwrite.
OVERWRITABLE_SOURCES = {
    CategorySource.RULE.value,
    CategorySource.MERCHANT.value,
    CategorySource.PROVIDER.value,
    CategorySource.UNCATEGORIZED.value,
}


class RuleMatchType(str, enum.Enum):
    EXACT_MERCHANT = "EXACT_MERCHANT"
    DESCRIPTION_CONTAINS = "DESCRIPTION_CONTAINS"
    AMOUNT_EQUALS = "AMOUNT_EQUALS"
    AMOUNT_RANGE = "AMOUNT_RANGE"
    ACCOUNT_ID = "ACCOUNT_ID"


# Match types driven by `match_value`; the rest are driven by the amount columns.
TEXT_MATCH_TYPES = {
    RuleMatchType.EXACT_MERCHANT.value,
    RuleMatchType.DESCRIPTION_CONTAINS.value,
    RuleMatchType.ACCOUNT_ID.value,
}
AMOUNT_MATCH_TYPES = {
    RuleMatchType.AMOUNT_EQUALS.value,
    RuleMatchType.AMOUNT_RANGE.value,
}


class BudgetPeriod(str, enum.Enum):
    MONTHLY = "MONTHLY"
    WEEKLY = "WEEKLY"
    QUARTERLY = "QUARTERLY"
    YEARLY = "YEARLY"


class RecurrenceFrequency(str, enum.Enum):
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUALLY = "ANNUALLY"


# Nominal gap between occurrences, in days. Used only to *classify* an observed
# interval; the actual next-date arithmetic is calendar-aware (see
# services/recurring.advance) so a monthly bill does not drift backwards through
# the year at 30 days a hop.
FREQUENCY_DAYS: dict[str, int] = {
    RecurrenceFrequency.WEEKLY.value: 7,
    RecurrenceFrequency.BIWEEKLY.value: 14,
    RecurrenceFrequency.MONTHLY.value: 30,
    RecurrenceFrequency.QUARTERLY.value: 91,
    RecurrenceFrequency.ANNUALLY.value: 365,
}


class StreamStatus(str, enum.Enum):
    """Lifecycle of a detected recurring stream.

    ACTIVE streams are the only ones the forecaster spends or receives money on.
    PAUSED means "expected to come back" (a gym membership on hold); CANCELLED
    means "gone", and both are honoured against re-detection — see
    `RecurringStream.status_source`.
    """

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class StatusSource(str, enum.Enum):
    """Who last set `recurring_stream.status`.

    The same invariant Phase 2 established for categories: a human decision
    outranks every automated pass. If the user cancels a subscription, the next
    detection run must not quietly resurrect it.
    """

    AUTO = "AUTO"
    USER = "USER"


class SecurityType(str, enum.Enum):
    """What kind of instrument a security is.

    This is the *instrument*, not the exposure — see `AssetClass`. A total-market
    ETF and a corporate-bond ETF are both ETF here and belong to different asset
    classes, which is precisely why the two axes are separate columns.
    """

    EQUITY = "EQUITY"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"
    CRYPTO = "CRYPTO"
    FIXED_INCOME = "FIXED_INCOME"
    CASH_EQUIVALENT = "CASH_EQUIVALENT"


class AssetClass(str, enum.Enum):
    """What a security is *exposed to*. The axis allocation is reported on.

    Deliberately not derivable from `SecurityType` alone: an ETF may hold US
    equity, international equity, bonds or REITs, so mapping instrument type
    straight onto exposure would file every fund into one bucket and report a
    diversified portfolio as "100% ETF" — a chart that answers the wrong
    question. `services/investment.classify_asset_class` infers this on sync;
    `security.asset_class_locked` protects a user's correction from the next
    run, the same invariant Phase 2 gave categories and Phase 3 gave status.
    """

    US_EQUITY = "US_EQUITY"
    INTERNATIONAL_EQUITY = "INTERNATIONAL_EQUITY"
    FIXED_INCOME = "FIXED_INCOME"
    CRYPTO = "CRYPTO"
    REAL_ESTATE = "REAL_ESTATE"
    CASH = "CASH"


class InvestmentTransactionType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    INTEREST = "INTEREST"
    FEE = "FEE"
    TRANSFER = "TRANSFER"


# Movements that cross the account boundary rather than rearranging what is
# already inside it. A BUY converts cash into shares and leaves the account's
# total value unchanged; a TRANSFER changes it. Separating contribution from
# market return depends entirely on this distinction.
EXTERNAL_FLOW_TYPES = {
    InvestmentTransactionType.TRANSFER.value,
}


class GoalCategory(str, enum.Enum):
    EMERGENCY_FUND = "EMERGENCY_FUND"
    HOUSE_DOWN_PAYMENT = "HOUSE_DOWN_PAYMENT"
    FIRE = "FIRE"
    CAR_PURCHASE = "CAR_PURCHASE"
    CUSTOM = "CUSTOM"


class GoalStatus(str, enum.Enum):
    """Where a goal stands against its own deadline.

    `AT_RISK` vs `OFF_TRACK` draws the same line budgets draw between AT_RISK
    and OVER_PACING: one is a nudge, the other says the target date is not
    reachable at the current savings rate.
    """

    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    OFF_TRACK = "OFF_TRACK"
    ACHIEVED = "ACHIEVED"
    # No target date, so there is no pace to be behind.
    NO_DEADLINE = "NO_DEADLINE"


# Account types that count against net worth rather than towards it.
# services/net_worth.signed_balance_cents derives the sign from the account type
# rather than trusting the stored value: Plaid reports a card balance as a
# positive amount owed, while seeds/demo.py stores it negative, so the raw sign
# is not a reliable input.
LIABILITY_ACCOUNT_TYPES = {
    AccountType.credit.value,
    AccountType.loan.value,
}


class PacingStatus(str, enum.Enum):
    """Where a category is heading, not just where it is.

    Derived from *projected* spend so an overspend is visible on day 10 rather
    than on day 30 when it is too late to change behaviour.
    """

    NO_LIMIT = "NO_LIMIT"
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"  # projecting slightly over — yellow
    OVER_PACING = "OVER_PACING"  # projecting well over — red
    OVER_BUDGET = "OVER_BUDGET"  # already spent past the limit — red
