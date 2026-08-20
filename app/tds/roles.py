"""Who signs what.

The TDS is signed by more parties than it has widgets for. The shipped PDF has
placed fields for the sellers' and buyers' signature and initial lines, but the
three agent lines - two "Broker Representing Seller" and one "Broker Obtaining
the Offer" - are printed rules with no fields behind them at all.

Because the DocuSeal template is built from coordinates rather than inherited
from the AcroForm, those missing lines are simply declared here with rules
measured out of the PDF's own vector content (see scripts/extract_widgets.py),
and they come out as real signable fields.

On the count: the brief says five signer roles. Six are modelled here. The
difference is whether the second Seller and second Buyer lines are read as the
same role signing twice or as distinct parties. They are distinct parties - a
co-owner is a separate legal signatory with their own date - so they get their
own roles, and a submission simply omits the ones a given deal does not need.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["seller", "co_seller", "buyer", "co_buyer", "listing_agent", "selling_agent"]

ROLE_LABELS: dict[str, str] = {
    "seller": "Seller",
    "co_seller": "Co-Seller",
    "buyer": "Buyer",
    "co_buyer": "Co-Buyer",
    "listing_agent": "Agent (Broker Representing Seller)",
    "selling_agent": "Agent (Broker Obtaining the Offer)",
}

#: Roles a seller-side disclosure actually needs before the property is listed.
#: Buyers and the selling agent only sign at receipt, which is a later event.
SELLER_SIDE_ROLES: tuple[str, ...] = ("seller", "co_seller", "listing_agent")


@dataclass(frozen=True)
class RoleField:
    """A signable field, addressed by existing widget key or by raw geometry."""

    name: str
    role: str
    type: Literal["signature", "initials", "date", "text"]
    widget: str | None = None
    #: Used only when the PDF has no widget: (page, x0, y0, x1, y1) in points.
    rect: tuple[int, float, float, float, float] | None = None
    required: bool = True


# Fields the PDF already has widgets for.
ROLE_FIELDS: list[RoleField] = [
    # Per-page initials.
    RoleField("seller_initials_p1", "seller", "initials", widget="SellersInitialsPage1"),
    RoleField("seller_initials_date_p1", "seller", "date", widget="SellersDatePage1"),
    RoleField("buyer_initials_p1", "buyer", "initials", widget="BuyersInitialsPage1", required=False),
    RoleField("buyer_initials_date_p1", "buyer", "date", widget="BuyersDatePage1", required=False),
    RoleField("seller_initials_p2", "seller", "initials", widget="Sellers Initials"),
    RoleField("seller_initials_date_p2", "seller", "date", widget="Date_3"),
    RoleField("buyer_initials_p2", "buyer", "initials", widget="Buyers Initials_2", required=False),
    RoleField("buyer_initials_date_p2", "buyer", "date", widget="Date3_af_date", required=False),
    # Section IV - the seller's certification that the answers are true.
    RoleField("seller_signature", "seller", "signature", widget="Signature4"),
    RoleField("seller_signature_date", "seller", "date", widget="Date_5"),
    RoleField("co_seller_signature", "co_seller", "signature", widget="Signature5", required=False),
    RoleField("co_seller_signature_date", "co_seller", "date", widget="Date_6", required=False),
    # Section V - acknowledgment of receipt.
    RoleField("seller_ack", "seller", "signature", widget="Seller_3"),
    RoleField("seller_ack_date", "seller", "date", widget="Date_9"),
    RoleField("buyer_ack", "buyer", "signature", widget="Buyer", required=False),
    RoleField("buyer_ack_date", "buyer", "date", widget="Date_10", required=False),
    RoleField("co_seller_ack", "co_seller", "signature", widget="Seller_4", required=False),
    RoleField("co_seller_ack_date", "co_seller", "date", widget="Date_11", required=False),
    RoleField("co_buyer_ack", "co_buyer", "signature", widget="Buyer_2", required=False),
    RoleField("co_buyer_ack_date", "co_buyer", "date", widget="Date_12", required=False),
]

# Fields the PDF is missing. Rects are measured from the printed rules in the
# page's vector content, inset slightly so the value sits on the line, not under it.
ROLE_FIELDS += [
    # Section IV agent line, y = 365.4.
    RoleField("listing_agent_name_iv", "listing_agent", "text", rect=(3, 182.1, 366.4, 303.4, 379.4), required=False),
    RoleField("listing_agent_sign_iv", "listing_agent", "signature", rect=(3, 321.6, 366.4, 454.0, 379.4), required=False),
    RoleField("listing_agent_date_iv", "listing_agent", "date", rect=(3, 473.2, 366.4, 572.0, 379.4), required=False),
    # Section V agent line, y = 250.8.
    RoleField("listing_agent_name_v", "listing_agent", "text", rect=(3, 163.6, 251.8, 299.9, 264.8), required=False),
    RoleField("listing_agent_sign_v", "listing_agent", "signature", rect=(3, 317.6, 251.8, 483.5, 264.8), required=False),
    RoleField("listing_agent_date_v", "listing_agent", "date", rect=(3, 504.4, 251.8, 571.2, 264.8), required=False),
    # Selling agent line, y = 224.3.
    RoleField("selling_agent_name", "selling_agent", "text", rect=(3, 164.6, 225.3, 301.9, 238.3), required=False),
    RoleField("selling_agent_sign", "selling_agent", "signature", rect=(3, 317.6, 225.3, 483.5, 238.3), required=False),
    RoleField("selling_agent_date", "selling_agent", "date", rect=(3, 504.4, 225.3, 571.2, 238.3), required=False),
]

#: Widgets the application fills from deal metadata rather than from an answer.
SYSTEM_FIELDS: dict[str, str] = {
    "PropertyAddress": "property_address",
    "PropertyAddress#1": "property_address",
    "Property Address_2": "property_address",
    "Date": "disclosure_date",
    "Date#1": "disclosure_date",
    "Date_4": "disclosure_date",
}

ROLE_WIDGET_KEYS: set[str] = {f.widget for f in ROLE_FIELDS if f.widget}


def fields_for_roles(roles: tuple[str, ...]) -> list[RoleField]:
    return [f for f in ROLE_FIELDS if f.role in roles]
