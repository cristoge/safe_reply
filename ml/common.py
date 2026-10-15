"""Lo que comparten los scripts de ml/: datos, modelo definitivo y reglas de negocio."""

from pathlib import Path

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline, make_pipeline

DATA_DIR = Path(__file__).parent / "data" / "banking77"
SEED = 42


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = pd.read_csv(DATA_DIR / "train.csv")
    test = pd.read_csv(DATA_DIR / "test.csv")
    return train, test


def build_model() -> Pipeline:
    """Modelo definitivo: TF-IDF + regresión logística.

    Elegido frente a Naive Bayes, SVM (normal y calibrado), SGD, KNN y Random Forest:
    mejor F1 macro (0.898, empatado con los demás lineales) y da probabilidades
    sin calibrar aparte → sirven para el umbral de confianza.
    """
    return make_pipeline(
        # sublinear_tf: una palabra repetida no pesa el doble · bigramas: "not working", "top up"
        TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True),
        LogisticRegression(C=20, class_weight="balanced", max_iter=2000),
    )


# Reglas de negocio (apartado 5 del diseño). Provisional: en el paso 2 pasan a un YAML.
# ticket_type → (ruta por defecto, intenciones)
TICKET_TYPES: dict[str, tuple[str, list[str]]] = {
    "security_and_fraud": ("human", [
        "lost_or_stolen_card", "compromised_card", "lost_or_stolen_phone",
        "card_payment_not_recognised", "cash_withdrawal_not_recognised",
        "direct_debit_payment_not_recognised",
    ]),
    "wrong_charges_and_refunds": ("human", [
        "transaction_charged_twice", "extra_charge_on_statement", "request_refund",
        "Refund_not_showing_up", "reverted_card_payment?", "wrong_amount_of_cash_received",
        "card_payment_wrong_exchange_rate", "wrong_exchange_rate_for_cash_withdrawal",
    ]),
    "cards_ordering_and_activation": ("ai", [
        "activate_my_card", "card_arrival", "card_delivery_estimate", "card_about_to_expire",
        "get_physical_card", "order_physical_card", "getting_spare_card", "getting_virtual_card",
        "get_disposable_virtual_card", "disposable_card_limits", "card_linking",
        "apple_pay_or_google_pay", "supported_cards_and_currencies", "visa_or_mastercard",
    ]),
    "cards_usage_problems": ("ai", [
        "card_not_working", "virtual_card_not_working", "contactless_not_working",
        "declined_card_payment", "card_acceptance", "card_swallowed", "pin_blocked",
        "change_pin", "pending_card_payment", "card_payment_fee_charged",
    ]),
    "atms_and_cash": ("ai", [
        "atm_support", "declined_cash_withdrawal", "pending_cash_withdrawal",
        "cash_withdrawal_charge",
    ]),
    "transfers": ("ai", [
        "cancel_transfer", "declined_transfer", "failed_transfer", "pending_transfer",
        "transfer_timing", "transfer_fee_charged", "transfer_into_account",
        "transfer_not_received_by_recipient", "receiving_money", "beneficiary_not_allowed",
        "balance_not_updated_after_bank_transfer",
    ]),
    "top_ups": ("ai", [
        "automatic_top_up", "pending_top_up", "top_up_by_bank_transfer_charge",
        "top_up_by_card_charge", "top_up_by_cash_or_cheque", "top_up_failed", "top_up_limits",
        "top_up_reverted", "topping_up_by_card", "verify_top_up",
        "balance_not_updated_after_cheque_or_cash_deposit",
    ]),
    "currency_exchange": ("ai", [
        "exchange_charge", "exchange_rate", "exchange_via_app", "fiat_currency_support",
    ]),
    "identity_verification": ("ai", [
        "verify_my_identity", "why_verify_identity", "unable_to_verify_identity",
        "verify_source_of_funds",
    ]),
    "account_and_personal_details": ("ai", [
        "edit_personal_details", "passcode_forgotten", "terminate_account", "age_limit",
        "country_support",
    ]),
}

# Siempre persona aunque su tipo lo responda la IA
ALWAYS_HUMAN = {
    "transfer_not_received_by_recipient",  # posible dinero perdido
    "failed_transfer",  # posible dinero perdido
    "unable_to_verify_identity",  # cumplimiento normativo
    "verify_source_of_funds",  # cumplimiento normativo
    "passcode_forgotten",  # seguridad de acceso
    "terminate_account",  # gestión y retención del cliente
}

INTENT_TYPE = {i: t for t, (_, intents) in TICKET_TYPES.items() for i in intents}
INTENT_ROUTE = {
    i: "human" if i in ALWAYS_HUMAN else route
    for route, intents in TICKET_TYPES.values()
    for i in intents
}
