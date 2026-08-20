app_name = "pc_production"
app_title = "PC Production"
app_publisher = "TRPL Solutions"
app_description = "Perfect Craft Production Costing"
app_email = "jaweriabibi098@gmail.com"
app_license = "mit"

fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            [
                "dt",
                "in",
                [
                    "Stock Reconciliation",
                    "Stock Reconciliation Item",
                ],
            ],
            [
                "fieldname",
                "in",
                [
                    "custom_expected_per_qty_amount",
                    "custom_actual_qty",
                    "custom_adjustment_per_qty",
                    "custom_production_expenses",
                    "custom_item_valuation_rate",
                ],
            ],
        ],
    },
    {
        "dt": "Property Setter",
        "filters": [
            [
                "doc_type",
                "=",
                "Stock Reconciliation Item",
            ],
            [
                "field_name",
                "=",
                "valuation_rate",
            ],
            [
                "property",
                "=",
                "label",
            ],
        ],
    },
]


doctype_js = {
    "Stock Reconciliation":
        "public/js/stock_reconciliation.js"
}


doc_events = {
    "Stock Entry": {
        "before_validate":
            "pc_production.stock_entry.apply_monthly_production_overhead"
    },

    "Stock Reconciliation": {
        "before_validate":
            "pc_production.stock_reconciliation.apply_stock_reconciliation_calculation",

        "before_submit":
            "pc_production.stock_reconciliation.validate_stock_reconciliation",
    },
}
