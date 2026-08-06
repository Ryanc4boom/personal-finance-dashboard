"""Layer 3 — Plaid Personal Finance Category -> our taxonomy.

Plaid's PFC is itself two-level (`primary` / `detailed`), so the mapping is
mostly mechanical. Two things make it worth an explicit table rather than a
string transform:

* The shapes genuinely differ. Plaid folds rent and utilities into one primary
  (`RENT_AND_UTILITIES`) that we split, and splits merchandise into a dozen
  detailed values we mostly collapse into `shopping`.
* Plaid classifies debt servicing as `LOAN_PAYMENTS`, which we treat as
  TRANSFER, not spend — a credit-card payment is moving money the user already
  counted when the card was swiped. Getting this wrong double-counts every
  statement payment, so it is pinned here deliberately.

`DETAILED_TO_SLUG` misses fall back to `PRIMARY_TO_SLUG`, and a miss there falls
through to layer 4 (Uncategorized) rather than guessing.
"""

DETAILED_TO_SLUG: dict[str, str] = {
    # --- Income ---------------------------------------------------------- #
    "INCOME_WAGES": "income.paycheck",
    "INCOME_DIVIDENDS": "income.dividends",
    "INCOME_INTEREST_EARNED": "income.interest",
    "INCOME_RETIREMENT_PENSION": "income.other_income",
    "INCOME_TAX_REFUND": "income.refunds_and_reimbursements",
    "INCOME_UNEMPLOYMENT": "income.government_benefits",
    "INCOME_OTHER_INCOME": "income.other_income",
    # --- Transfers ------------------------------------------------------- #
    "TRANSFER_IN_DEPOSIT": "transfers.account_transfer",
    "TRANSFER_IN_SAVINGS": "transfers.savings_transfer",
    "TRANSFER_IN_ACCOUNT_TRANSFER": "transfers.account_transfer",
    "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS": "transfers.investment_contribution",
    "TRANSFER_IN_CASH_ADVANCES_AND_LOANS": "transfers.account_transfer",
    "TRANSFER_IN_OTHER_TRANSFER_IN": "transfers.account_transfer",
    "TRANSFER_OUT_SAVINGS": "transfers.savings_transfer",
    "TRANSFER_OUT_ACCOUNT_TRANSFER": "transfers.account_transfer",
    "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS": "transfers.investment_contribution",
    "TRANSFER_OUT_WITHDRAWAL": "transfers.cash_and_atm",
    "TRANSFER_OUT_OTHER_TRANSFER_OUT": "transfers.account_transfer",
    # --- Loan payments (debt servicing, not spend) ------------------------ #
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": "transfers.credit_card_payment",
    "LOAN_PAYMENTS_CAR_PAYMENT": "transportation.auto_payment",
    "LOAN_PAYMENTS_MORTGAGE_PAYMENT": "housing.mortgage",
    "LOAN_PAYMENTS_STUDENT_LOAN_PAYMENT": "education.student_loans",
    "LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT": "transfers.loan_payment",
    "LOAN_PAYMENTS_OTHER_PAYMENT": "transfers.loan_payment",
    # --- Bank fees -------------------------------------------------------- #
    "BANK_FEES_ATM_FEES": "financial.atm_fees",
    "BANK_FEES_FOREIGN_TRANSACTION_FEES": "financial.foreign_transaction_fees",
    "BANK_FEES_INSUFFICIENT_FUNDS": "financial.bank_fees",
    "BANK_FEES_INTEREST_CHARGE": "financial.interest_charges",
    "BANK_FEES_OVERDRAFT_FEES": "financial.bank_fees",
    "BANK_FEES_LATE_PAYMENT": "financial.late_fees",
    "BANK_FEES_OTHER_BANK_FEES": "financial.bank_fees",
    # --- Entertainment ---------------------------------------------------- #
    "ENTERTAINMENT_CASINOS_AND_GAMBLING": "entertainment.games",
    "ENTERTAINMENT_MUSIC_AND_AUDIO": "entertainment.music",
    "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS": "entertainment.events_and_concerts",
    "ENTERTAINMENT_TV_AND_MOVIES": "entertainment.movies_and_tv",
    "ENTERTAINMENT_VIDEO_GAMES": "entertainment.games",
    "ENTERTAINMENT_OTHER_ENTERTAINMENT": "entertainment",
    # --- Food & drink ------------------------------------------------------ #
    "FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR": "food_drink.bars_and_alcohol",
    "FOOD_AND_DRINK_COFFEE": "food_drink.coffee_shops",
    "FOOD_AND_DRINK_FAST_FOOD": "food_drink.fast_food",
    "FOOD_AND_DRINK_GROCERIES": "food_drink.groceries",
    "FOOD_AND_DRINK_RESTAURANT": "food_drink.restaurants",
    "FOOD_AND_DRINK_VENDING_MACHINES": "food_drink.fast_food",
    "FOOD_AND_DRINK_OTHER_FOOD_AND_DRINK": "food_drink",
    # --- General merchandise ------------------------------------------------ #
    "GENERAL_MERCHANDISE_BOOKSTORES_AND_NEWSSTANDS": "shopping.books",
    "GENERAL_MERCHANDISE_CLOTHING_AND_ACCESSORIES": "shopping.clothing",
    "GENERAL_MERCHANDISE_CONVENIENCE_STORES": "food_drink.groceries",
    "GENERAL_MERCHANDISE_DEPARTMENT_STORES": "shopping.department_stores",
    "GENERAL_MERCHANDISE_DISCOUNT_STORES": "shopping.department_stores",
    "GENERAL_MERCHANDISE_ELECTRONICS": "shopping.electronics",
    "GENERAL_MERCHANDISE_GIFTS_AND_NOVELTIES": "shopping.gifts",
    "GENERAL_MERCHANDISE_OFFICE_SUPPLIES": "shopping.office_supplies",
    "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES": "shopping.online_marketplace",
    "GENERAL_MERCHANDISE_PET_SUPPLIES": "shopping.pet_supplies",
    "GENERAL_MERCHANDISE_SPORTING_GOODS": "shopping.sporting_goods",
    "GENERAL_MERCHANDISE_SUPERSTORES": "shopping.department_stores",
    "GENERAL_MERCHANDISE_TOBACCO_AND_VAPE": "shopping",
    "GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE": "shopping",
    # --- Home improvement --------------------------------------------------- #
    "HOME_IMPROVEMENT_FURNITURE": "housing.furniture",
    "HOME_IMPROVEMENT_HARDWARE": "housing.home_improvement",
    "HOME_IMPROVEMENT_REPAIR_AND_MAINTENANCE": "housing.home_maintenance",
    "HOME_IMPROVEMENT_SECURITY": "housing.home_maintenance",
    "HOME_IMPROVEMENT_OTHER_HOME_IMPROVEMENT": "housing.home_improvement",
    # --- Medical ------------------------------------------------------------ #
    "MEDICAL_DENTAL_CARE": "health.dentist",
    "MEDICAL_EYE_CARE": "health.vision",
    "MEDICAL_NURSING_CARE": "health.doctor",
    "MEDICAL_PHARMACIES_AND_SUPPLEMENTS": "health.pharmacy",
    "MEDICAL_PRIMARY_CARE": "health.doctor",
    "MEDICAL_VETERINARY_SERVICES": "health.veterinary",
    "MEDICAL_OTHER_MEDICAL": "health",
    # --- Personal care ------------------------------------------------------ #
    "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS": "health.fitness_and_gym",
    "PERSONAL_CARE_HAIR_AND_BEAUTY": "health.personal_care",
    "PERSONAL_CARE_LAUNDRY_AND_DRY_CLEANING": "health.personal_care",
    "PERSONAL_CARE_OTHER_PERSONAL_CARE": "health.personal_care",
    # --- General services --------------------------------------------------- #
    "GENERAL_SERVICES_ACCOUNTING_AND_FINANCIAL_PLANNING": "financial.financial_advisory",
    "GENERAL_SERVICES_AUTOMOTIVE": "transportation.auto_maintenance",
    "GENERAL_SERVICES_CHILDCARE": "education.childcare",
    "GENERAL_SERVICES_CONSULTING_AND_LEGAL": "financial.legal",
    "GENERAL_SERVICES_EDUCATION": "education.courses_and_training",
    "GENERAL_SERVICES_INSURANCE": "financial.insurance",
    "GENERAL_SERVICES_POSTAGE_AND_SHIPPING": "shopping.office_supplies",
    "GENERAL_SERVICES_STORAGE": "housing.household_supplies",
    "GENERAL_SERVICES_OTHER_GENERAL_SERVICES": "shopping",
    # --- Government & non-profit -------------------------------------------- #
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": "financial.charity_and_donations",
    "GOVERNMENT_AND_NON_PROFIT_GOVERNMENT_DEPARTMENTS_AND_AGENCIES": "financial.taxes",
    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT": "financial.taxes",
    "GOVERNMENT_AND_NON_PROFIT_OTHER_GOVERNMENT_AND_NON_PROFIT": "financial",
    # --- Transportation ------------------------------------------------------ #
    "TRANSPORTATION_BIKES_AND_SCOOTERS": "transportation.public_transit",
    "TRANSPORTATION_GAS": "transportation.gas_and_fuel",
    "TRANSPORTATION_PARKING": "transportation.parking",
    "TRANSPORTATION_PUBLIC_TRANSIT": "transportation.public_transit",
    "TRANSPORTATION_TAXIS_AND_RIDE_SHARES": "transportation.rideshare_and_taxi",
    "TRANSPORTATION_TOLLS": "transportation.tolls",
    "TRANSPORTATION_OTHER_TRANSPORTATION": "transportation",
    # --- Travel --------------------------------------------------------------- #
    "TRAVEL_FLIGHTS": "travel.flights",
    "TRAVEL_LODGING": "travel.hotels",
    "TRAVEL_RENTAL_CARS": "travel.car_rental",
    "TRAVEL_OTHER_TRAVEL": "travel",
    # --- Rent & utilities ------------------------------------------------------ #
    "RENT_AND_UTILITIES_RENT": "housing.rent",
    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY": "utilities.electricity",
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": "utilities.internet",
    "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT": "utilities.water_and_sewer",
    "RENT_AND_UTILITIES_TELEPHONE": "utilities.mobile_phone",
    "RENT_AND_UTILITIES_WATER": "utilities.water_and_sewer",
    "RENT_AND_UTILITIES_OTHER_UTILITIES": "utilities",
}

PRIMARY_TO_SLUG: dict[str, str] = {
    "INCOME": "income",
    "TRANSFER_IN": "transfers",
    "TRANSFER_OUT": "transfers",
    "LOAN_PAYMENTS": "transfers.loan_payment",
    "BANK_FEES": "financial.bank_fees",
    "ENTERTAINMENT": "entertainment",
    "FOOD_AND_DRINK": "food_drink",
    "GENERAL_MERCHANDISE": "shopping",
    "HOME_IMPROVEMENT": "housing.home_improvement",
    "MEDICAL": "health",
    "PERSONAL_CARE": "health.personal_care",
    "GENERAL_SERVICES": "shopping",
    "GOVERNMENT_AND_NON_PROFIT": "financial",
    "TRANSPORTATION": "transportation",
    "TRAVEL": "travel",
    "RENT_AND_UTILITIES": "utilities",
}


def slug_for_provider_category(provider_category: str | None) -> str | None:
    """Map a stored PFC slug to one of ours, or None if we have no opinion."""
    if not provider_category:
        return None

    key = provider_category.strip().upper()
    if key in DETAILED_TO_SLUG:
        return DETAILED_TO_SLUG[key]

    # Unknown detailed value under a primary we do know — Plaid adds detailed
    # values over time, so degrade to the parent rather than dropping to
    # Uncategorized.
    for primary, slug in PRIMARY_TO_SLUG.items():
        if key.startswith(primary):
            return slug

    return None
