import json
from collections import defaultdict
from pathlib import Path

import frappe
from frappe.utils import flt, getdate, strip_html_tags


PRINT_FORMAT_NAME = "Perfect Craft Sales Order Two Copy"


def _money(value):
    return f"{flt(value):,.2f}"


def _qty(value):
    value = flt(value)

    if abs(value - round(value)) < 0.000001:
        return str(int(round(value)))

    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _date(value):
    if not value:
        return ""

    return getdate(value).strftime("%d-%m-%Y")


def _get_address(doc):
    city = ""
    address = ""

    if doc.get("customer_address"):
        address_doc = frappe.get_doc(
            "Address",
            doc.customer_address,
        )

        address_parts = [
            address_doc.address_line1,
            address_doc.address_line2,
        ]

        address = " ".join(
            part.strip()
            for part in address_parts
            if part and part.strip()
        )

        city = (
            address_doc.city
            or ""
        )

    elif doc.get("address_display"):
        address = strip_html_tags(
            doc.address_display
        ).replace(
            "\n",
            " "
        ).strip()

    return address, city


def _get_sales_person(doc):
    if doc.get("sales_team"):
        first_row = doc.sales_team[0]

        return (
            first_row.sales_person
            or ""
        )

    return ""


def _get_item_tax_amounts(doc):
    """
    ERPNext v15 stores item-wise tax breakup
    inside Sales Taxes and Charges.
    """

    result = defaultdict(float)

    for tax_row in doc.get("taxes") or []:
        raw_detail = (
            tax_row.get(
                "item_wise_tax_detail"
            )
        )

        if not raw_detail:
            continue

        if isinstance(
            raw_detail,
            str,
        ):
            try:
                detail = json.loads(
                    raw_detail
                )
            except Exception:
                continue
        else:
            detail = raw_detail

        if not isinstance(
            detail,
            dict,
        ):
            continue

        for key, value in detail.items():
            amount = 0

            if isinstance(
                value,
                (
                    list,
                    tuple,
                ),
            ):
                if len(value) >= 2:
                    amount = flt(
                        value[1]
                    )

            elif isinstance(
                value,
                dict,
            ):
                amount = flt(
                    value.get(
                        "tax_amount"
                    )
                    or value.get(
                        "amount"
                    )
                )

            result[key] += amount

    return result


def _get_last_payment(
    company,
    customer,
    as_on_date,
):
    rows = frappe.db.sql(
        """
        SELECT
            posting_date,
            base_paid_amount,
            paid_amount
        FROM `tabPayment Entry`
        WHERE
            docstatus = 1
            AND company = %s
            AND party_type = 'Customer'
            AND party = %s
            AND payment_type = 'Receive'
            AND posting_date <= %s
        ORDER BY
            posting_date DESC,
            creation DESC
        LIMIT 1
        """,
        (
            company,
            customer,
            as_on_date,
        ),
        as_dict=True,
    )

    if not rows:
        return {
            "date": "",
            "amount": "0.00",
        }

    row = rows[0]

    amount = (
        flt(
            row.base_paid_amount
        )
        or flt(
            row.paid_amount
        )
    )

    return {
        "date":
            _date(
                row.posting_date
            ),
        "amount":
            _money(
                amount
            ),
    }


def _get_customer_balance(
    company,
    customer,
    as_on_date,
):
    result = frappe.db.sql(
        """
        SELECT
            COALESCE(
                SUM(debit - credit),
                0
            ) AS balance
        FROM `tabGL Entry`
        WHERE
            company = %s
            AND party_type = 'Customer'
            AND party = %s
            AND posting_date <= %s
            AND is_cancelled = 0
        """,
        (
            company,
            customer,
            as_on_date,
        ),
        as_dict=True,
    )

    balance = (
        flt(
            result[0].balance
        )
        if result
        else 0
    )

    return {
        "amount":
            _money(
                abs(balance)
            ),
        "type":
            (
                "Dr."
                if balance >= 0
                else "Cr."
            ),
    }


def _get_aging_days(
    company,
    customer,
    as_on_date,
):
    rows = frappe.db.sql(
        """
        SELECT posting_date
        FROM `tabSales Invoice`
        WHERE
            docstatus = 1
            AND company = %s
            AND customer = %s
            AND posting_date <= %s
            AND outstanding_amount > 0
        ORDER BY posting_date ASC
        LIMIT 1
        """,
        (
            company,
            customer,
            as_on_date,
        ),
        as_dict=True,
    )

    if not rows:
        return 0

    invoice_date = getdate(
        rows[0].posting_date
    )

    current_date = getdate(
        as_on_date
    )

    days = (
        current_date
        - invoice_date
    ).days

    return max(
        days,
        0,
    )


def get_sales_order_print_context(doc):
    address, city = (
        _get_address(
            doc
        )
    )

    tax_map = (
        _get_item_tax_amounts(
            doc
        )
    )

    items = []

    total_qty = 0
    total_gross = 0
    total_tax = 0
    total_net = 0

    for row in doc.get("items") or []:
        gross_amount = flt(
            row.amount
        )

        tax_amount = flt(
            tax_map.get(
                row.item_code
            )
        )

        # Some ERPNext tax maps may use
        # child row name instead of item code.
        if not tax_amount:
            tax_amount = flt(
                tax_map.get(
                    row.name
                )
            )

        net_amount = (
            gross_amount
            + tax_amount
        )

        total_qty += flt(
            row.qty
        )

        total_gross += (
            gross_amount
        )

        total_tax += (
            tax_amount
        )

        total_net += (
            net_amount
        )

        items.append(
            frappe._dict(
                {
                    "idx":
                        row.idx,
                    "description":
                        (
                            row.item_name
                            or row.item_code
                            or ""
                        ).upper(),
                    "qty":
                        _qty(
                            row.qty
                        ),
                    "uom":
                        (
                            row.uom
                            or row.stock_uom
                            or ""
                        ),
                    "rate":
                        _money(
                            row.rate
                        ),
                    "gross":
                        _money(
                            gross_amount
                        ),
                    "tax":
                        _money(
                            tax_amount
                        ),
                    "net":
                        _money(
                            net_amount
                        ),
                }
            )
        )

    as_on_date = (
        doc.transaction_date
        or frappe.utils.today()
    )

    last_payment = (
        _get_last_payment(
            doc.company,
            doc.customer,
            as_on_date,
        )
    )

    balance = (
        _get_customer_balance(
            doc.company,
            doc.customer,
            as_on_date,
        )
    )

    return frappe._dict(
        {
            "order_no":
                doc.name,
            "date":
                _date(
                    as_on_date
                ),
            "customer":
                (
                    doc.customer_name
                    or doc.customer
                    or ""
                ).upper(),
            "address":
                address.upper(),
            "region":
                (
                    doc.territory
                    or ""
                ).upper(),
            "city":
                city.upper(),
            "sales_person":
                _get_sales_person(
                    doc
                ).upper(),
            "items":
                items,
            "total_qty":
                _qty(
                    total_qty
                ),
            "total_gross":
                _money(
                    total_gross
                ),
            "total_tax":
                _money(
                    total_tax
                ),
            "total_net":
                _money(
                    total_net
                ),
            "last_payment_date":
                last_payment[
                    "date"
                ],
            "last_payment_amount":
                last_payment[
                    "amount"
                ],
            "balance_date":
                _date(
                    as_on_date
                ),
            "balance_amount":
                balance[
                    "amount"
                ],
            "balance_type":
                balance[
                    "type"
                ],
            "aging_days":
                _get_aging_days(
                    doc.company,
                    doc.customer,
                    as_on_date,
                ),
        }
    )


def ensure_sales_order_print_format():
    html_path = frappe.get_app_path(
        "pc_production",
        "print_formats",
        "perfect_craft_sales_order_two_copy.html",
    )

    html = Path(
        html_path
    ).read_text(
        encoding="utf-8"
    )

    if frappe.db.exists(
        "Print Format",
        PRINT_FORMAT_NAME,
    ):
        print_format = frappe.get_doc(
            "Print Format",
            PRINT_FORMAT_NAME,
        )

    else:
        print_format = frappe.new_doc(
            "Print Format"
        )

        print_format.name = (
            PRINT_FORMAT_NAME
        )

    print_format.doc_type = (
        "Sales Order"
    )

    print_format.print_format_type = (
        "Jinja"
    )

    print_format.custom_format = 1
    print_format.standard = "No"
    print_format.disabled = 0
    print_format.html = html

    print_format.save(
        ignore_permissions=True
    )
