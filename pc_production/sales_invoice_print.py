import json
from collections import defaultdict
from pathlib import Path

import frappe
from frappe.utils import (
    flt,
    getdate,
    get_time,
    money_in_words,
    strip_html_tags,
)


PRINT_FORMAT_NAME = "Perfect Craft Sales Invoice Two Copy"


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

    return getdate(value).strftime("%d/%m/%Y")


def _time(value):
    if not value:
        return ""

    try:
        return get_time(value).strftime("%I:%M %p")
    except Exception:
        return str(value)


def _first_value(doc, *fieldnames):
    for fieldname in fieldnames:
        value = doc.get(fieldname)

        if value not in (None, ""):
            return value

    return ""


def _get_address(doc):
    address = ""
    city = ""

    address_name = (
        doc.get("customer_address")
        or doc.get("billing_address_name")
    )

    if address_name:
        address_doc = frappe.get_doc(
            "Address",
            address_name,
        )

        parts = [
            address_doc.address_line1,
            address_doc.address_line2,
        ]

        address = " ".join(
            str(part).strip()
            for part in parts
            if part
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
        return (
            doc.sales_team[0].sales_person
            or ""
        )

    return ""


def _get_sales_order(doc):
    sales_orders = []

    for row in doc.get("items") or []:
        sales_order = row.get(
            "sales_order"
        )

        if (
            sales_order
            and sales_order not in sales_orders
        ):
            sales_orders.append(
                sales_order
            )

    if sales_orders:
        return ", ".join(
            sales_orders
        )

    return _first_value(
        doc,
        "custom_order_no",
        "order_no",
        "po_no",
    )


def _get_delivery_note(doc):
    for row in doc.get("items") or []:
        delivery_note = row.get(
            "delivery_note"
        )

        if delivery_note:
            return delivery_note

    return ""


def _get_transport_details(doc):
    gp_no = _first_value(
        doc,
        "custom_gp_no",
        "custom_gate_pass_no",
        "custom_gate_pass",
        "gate_pass_no",
        "gp_no",
    )

    driver_name = _first_value(
        doc,
        "custom_driver_name",
        "driver_name",
        "custom_driver",
        "driver",
    )

    vehicle_no = _first_value(
        doc,
        "custom_vehicle_no",
        "custom_vehicle_number",
        "vehicle_no",
        "vehicle_number",
    )

    delivery_note = _get_delivery_note(
        doc
    )

    if (
        delivery_note
        and frappe.db.exists(
            "Delivery Note",
            delivery_note,
        )
    ):
        dn = frappe.get_doc(
            "Delivery Note",
            delivery_note,
        )

        if not gp_no:
            gp_no = _first_value(
                dn,
                "custom_gp_no",
                "custom_gate_pass_no",
                "custom_gate_pass",
                "gate_pass_no",
                "gp_no",
            )

        if not driver_name:
            driver_name = _first_value(
                dn,
                "custom_driver_name",
                "driver_name",
                "custom_driver",
                "driver",
            )

        if not vehicle_no:
            vehicle_no = _first_value(
                dn,
                "custom_vehicle_no",
                "custom_vehicle_number",
                "vehicle_no",
                "vehicle_number",
            )

    return (
        gp_no,
        driver_name,
        vehicle_no,
    )


def _get_item_tax_amounts(doc):
    result = defaultdict(float)

    for tax_row in doc.get("taxes") or []:
        raw_detail = tax_row.get(
            "item_wise_tax_detail"
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
                (list, tuple),
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


def _amount_in_words(doc):
    text = (
        doc.get("in_words")
        or money_in_words(
            doc.grand_total,
            doc.currency,
        )
        or ""
    )

    text = strip_html_tags(
        text
    ).strip()

    currency = (
        doc.currency
        or ""
    ).strip()

    if (
        currency
        and text.upper().startswith(
            currency.upper()
        )
    ):
        text = text[
            len(currency):
        ].strip()

    text = text.rstrip(
        ". "
    )

    if text.lower().endswith(
        " only"
    ):
        text = text[:-5].strip()

    return text.upper()


def get_sales_invoice_print_context(doc):
    address, city = (
        _get_address(
            doc
        )
    )

    (
        gp_no,
        driver_name,
        vehicle_no,
    ) = _get_transport_details(
        doc
    )

    tax_map = (
        _get_item_tax_amounts(
            doc
        )
    )

    items = []

    for row in doc.get("items") or []:
        gross_amount = flt(
            row.amount
        )

        tax_amount = flt(
            tax_map.get(
                row.item_code
            )
        )

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

    gross_total = flt(
        doc.total
    )

    tax_total = flt(
        doc.total_taxes_and_charges
    )

    net_total = flt(
        doc.grand_total
    )

    if not gross_total:
        gross_total = sum(
            flt(row.amount)
            for row
            in doc.get("items") or []
        )

    if not net_total:
        net_total = (
            gross_total
            + tax_total
        )

    return frappe._dict(
        {
            "gp_no":
                gp_no,

            "order_no":
                _get_sales_order(
                    doc
                ),

            "date":
                _date(
                    doc.posting_date
                ),

            "time":
                _time(
                    doc.posting_time
                ),

            "customer":
                (
                    doc.customer_name
                    or doc.customer
                    or ""
                ).upper(),

            "address":
                (
                    address
                    or ""
                ).upper(),

            "region":
                (
                    doc.territory
                    or ""
                ).upper(),

            "city":
                (
                    city
                    or ""
                ).upper(),

            "driver_name":
                (
                    driver_name
                    or ""
                ).upper(),

            "vehicle_no":
                (
                    vehicle_no
                    or ""
                ).upper(),

            "sales_person":
                _get_sales_person(
                    doc
                ).upper(),

            "items":
                items,

            "total_qty":
                _qty(
                    doc.total_qty
                ),

            "total_gross":
                _money(
                    gross_total
                ),

            "total_tax":
                _money(
                    tax_total
                ),

            "total_net":
                _money(
                    net_total
                ),

            "amount_in_words":
                _amount_in_words(
                    doc
                ),
        }
    )


def ensure_sales_invoice_print_format():
    html_path = frappe.get_app_path(
        "pc_production",
        "print_formats",
        "perfect_craft_sales_invoice_two_copy.html",
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
        "Sales Invoice"
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
