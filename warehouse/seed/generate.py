"""Generate a deterministic seed dataset for the semantic layer.

Same seed in, byte-identical CSVs out — so a parity test can assert on a number and keep asserting
on it. That is the whole reason this is generated rather than random.

The data deliberately reproduces the traps the model documents, because a demo where every column
is populated and every join is 1:1 teaches the opposite of what the layer is for:

  * commission_ledger repeats the SAME amount across a sale's installments, so a naive
    SUM over-counts — the reason the canonical total lives at sale grain.
  * lost_reason is NULL unless the deal was lost, and closed_at is NULL while it is open:
    NULL as semantics, not as a data gap (see meta.coverage on those members).
  * a few orders are self-purchases and a few are children of another order, so the canonical
    order count genuinely differs from the raw row count.
  * the recruitment funnel drops off between stages, so conversion rates are not 100%.

Run:
    python warehouse/seed/generate.py           # writes warehouse/seed/data/*.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCHEMA = json.loads((ROOT / "warehouse" / "schema.json").read_text(encoding="utf-8"))
OUT = ROOT / "warehouse" / "seed" / "data"

SEED = 20260816
RNG = random.Random(SEED)

START = dt.date(2024, 1, 1)
END = dt.date(2026, 6, 30)

PRODUCT_LINES = ["Appliances", "Furniture", "Electronics"]
LINE_TO_MART = {"Appliances": "mart_appliances", "Furniture": "mart_furniture",
                "Electronics": "mart_electronics"}

N_REPS = 150
N_CUSTOMERS = 600
N_PRODUCTS = 300
N_SUPPLIERS = 40
N_SALES = 6000
N_ORDERS = 9000
N_QUOTES = 12000
N_DEALS_PER_LINE = 1200
N_LEADS = 2000


def rand_date(start: dt.date = START, end: dt.date = END) -> dt.date:
    """Pick a date uniformly in a range.

    Args:
        start (dt.date): First allowed date.
        end (dt.date): Last allowed date.

    Returns:
        dt.date: The chosen date.
    """
    return start + dt.timedelta(days=RNG.randint(0, (end - start).days))


def ts(d: dt.date) -> str:
    """Render a date as a warehouse timestamp literal.

    Args:
        d (dt.date): The date to render.

    Returns:
        str: An ISO timestamp at a plausible business hour.
    """
    return f"{d.isoformat()} {RNG.randint(8, 19):02d}:{RNG.randint(0, 59):02d}:00"


def month_start(d: dt.date) -> str:
    """Truncate a date to the first day of its month.

    Args:
        d (dt.date): The date to truncate.

    Returns:
        str: The month start as a timestamp literal.
    """
    return f"{d.year:04d}-{d.month:02d}-01 00:00:00"


def money(low: float, high: float) -> str:
    """Draw a monetary amount.

    Args:
        low (float): Lower bound.
        high (float): Upper bound.

    Returns:
        str: The amount with two decimals.
    """
    return f"{RNG.uniform(low, high):.2f}"


def write(table: str, rows: list[dict]) -> None:
    """Write one table's rows as CSV, with the columns the schema declares.

    Any schema column a row omits is written empty (NULL). That is what lets the seed express
    "NULL is the semantics here" instead of inventing a value.

    Args:
        table (str): Fully-qualified table reference from schema.json.
        rows (list[dict]): The rows to write.
    """
    cols = sorted(SCHEMA[table])
    OUT.mkdir(parents=True, exist_ok=True)
    name = table.replace(".", "__")
    with (OUT / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in cols})
    print(f"  {len(rows):>6} linhas  {name}.csv")


# --------------------------------------------------------------------------- entities

def gen_reps() -> list[dict]:
    """Build the sales-rep roster.

    Agencies share a company_id (several reps per agency), which is why counting agencies uses
    distinct company_id and not a rep count.

    Returns:
        list[dict]: The rep rows.
    """
    tiers = ["Independent"] * 90 + ["Associate"] * 40 + ["Agency"] * 20
    RNG.shuffle(tiers)
    statuses = ["Active"] * 95 + ["Suspended"] * 15 + ["Churned"] * 25 + ["Pending"] * 15
    RNG.shuffle(statuses)
    regions = ["Sudeste", "Sul", "Nordeste", "Centro-Oeste", "Norte"]
    cities = ["São Paulo", "Campinas", "Porto Alegre", "Curitiba", "Recife", "Salvador",
              "Belo Horizonte", "Goiânia", "Manaus", "Fortaleza"]
    managers = [f"AM {n}" for n in ("Alves", "Barros", "Castro", "Dias", "Esteves")]
    rows = []
    for i in range(N_REPS):
        tier = tiers[i]
        status = statuses[i]
        onboard = rand_date(START, END - dt.timedelta(days=60))
        # An agency groups several reps under one company; an independent rep is their own company.
        company = f"AG{(i % 20) + 1:03d}" if tier in ("Associate", "Agency") else f"IND{i:04d}"
        rows.append({
            "rep_id": f"REP{i:04d}",
            "full_name": f"Rep {i:04d}",
            "email": f"rep{i:04d}@meridian-supply.example",
            "phone": f"+55 11 9{RNG.randint(1000, 9999)}-{RNG.randint(1000, 9999)}",
            "tax_id": f"{RNG.randint(10**10, 10**11 - 1)}",
            "tier": tier,
            "rep_status": status,
            "cohort": f"{onboard.year}-Q{(onboard.month - 1) // 3 + 1}",
            "region": RNG.choice(regions),
            "territory": f"T{RNG.randint(1, 12):02d}",
            "city": RNG.choice(cities),
            "state": RNG.choice(["SP", "RS", "PR", "PE", "BA", "MG", "GO", "AM", "CE"]),
            "account_manager": RNG.choice(managers),
            "lead_tier": RNG.choice(["Premium", "Standard", "Referral", "Outbound"]),
            "company_id": company,
            "plan": RNG.choice(["Annual", "Monthly"]),
            "rep_score": RNG.randint(0, 100),
            "onboarding_date": ts(onboard),
            # Only a churned rep has left, so exit_date is NULL for everyone else by definition.
            "exit_date": ts(rand_date(onboard, END)) if status == "Churned" else "",
            # A rep who never signed in has no last login — absence is the fact, not a gap.
            "last_portal_login": ts(rand_date(onboard, END)) if RNG.random() > 0.12 else "",
            "crm_contact_id": f"CRM{i:06d}",
            "billing_customer_id": f"BILL{i:05d}",
        })
    return rows


def gen_customers() -> list[dict]:
    """Build the customer base.

    Returns:
        list[dict]: The customer rows.
    """
    industries = ["Varejo", "Construção", "Hotelaria", "Educação", "Saúde", "Indústria"]
    return [{
        "customer_id": f"CUS{i:05d}",
        "company_name": f"Cliente {i:05d} Ltda",
        "segment": RNG.choice(["Enterprise", "MidMarket", "SMB"]),
        "industry": RNG.choice(industries),
        "state": RNG.choice(["SP", "RS", "PR", "PE", "BA", "MG", "GO", "AM", "CE"]),
        "is_active": RNG.random() > 0.18,
        "created_at": ts(rand_date()),
    } for i in range(N_CUSTOMERS)]


def gen_products() -> list[dict]:
    """Build the product catalog, with parent products and their variants.

    parent_product_id is NULL on a parent and populated on a variant — the routing the model
    exposes through is_parent_product / product_level.

    Returns:
        list[dict]: The product rows.
    """
    categories = {
        "Appliances": ["Refrigeração", "Cocção", "Lavanderia"],
        "Furniture": ["Escritório", "Residencial", "Corporativo"],
        "Electronics": ["Áudio", "Vídeo", "Informática"],
    }
    rows = []
    n_parents = N_PRODUCTS // 3
    for i in range(N_PRODUCTS):
        line = PRODUCT_LINES[i % 3]
        is_parent = i < n_parents
        rows.append({
            "product_id": i + 1,
            "product_name": f"{line[:4].upper()}-{i + 1:04d}",
            "product_line": line,
            "category": RNG.choice(categories[line]),
            "parent_product_id": "" if is_parent else RNG.randint(1, n_parents),
            "supplier_id": f"SUP{RNG.randint(1, N_SUPPLIERS):03d}",
            "list_price": money(120, 9000),
            "is_active": RNG.random() > 0.1,
        })
    return rows


# --------------------------------------------------------------------------- facts

def gen_sales(rep_ids: list[str], customer_ids: list[str],
              products: list[dict]) -> tuple[list[dict], list[dict]]:
    """Build sales at SALE grain, plus the commission ledger at installment grain.

    The ledger repeats the sale's commission amount on every installment row. A SUM there
    over-counts, which is exactly why the canonical commission total is read from the sale grain.

    Args:
        rep_ids (list[str]): Rep ids to draw from.
        customer_ids (list[str]): Customer ids to draw from.
        products (list[dict]): Product rows, used to keep product_line consistent.

    Returns:
        tuple[list[dict], list[dict]]: (sales rows, commission ledger rows).
    """
    sales, ledger = [], []
    for i in range(N_SALES):
        product = RNG.choice(products)
        sold = rand_date()
        amount = float(money(500, 45000))
        total_commission = round(amount * RNG.uniform(0.04, 0.11), 2)
        rep_share = round(total_commission * RNG.uniform(0.55, 0.8), 2)
        sale_id = f"SAL{i:06d}"
        sales.append({
            "sale_id": sale_id,
            "rep_id": RNG.choice(rep_ids),
            "customer_id": RNG.choice(customer_ids),
            "product_id": product["product_id"],
            "product_line": product["product_line"],
            "channel": RNG.choice(["Direct", "Marketplace", "Inbound"]),
            "is_new_customer": RNG.random() < 0.28,
            "sale_amount": f"{amount:.2f}",
            "commission_total_amount": f"{total_commission:.2f}",
            "commission_rep_amount": f"{rep_share:.2f}",
            "commission_company_amount": f"{total_commission - rep_share:.2f}",
            "sold_at": ts(sold),
        })
        # THE TRAP: one row per installment, each carrying the FULL sale commission.
        installments = RNG.choice([1, 1, 2, 3, 6, 12])
        for n in range(1, installments + 1):
            settled = RNG.random() < 0.7
            ledger.append({
                "commission_id": f"COM{i:06d}-{n:02d}",
                "sale_id": sale_id,
                "rep_id": sales[-1]["rep_id"],
                "product_line": product["product_line"],
                "installment": n,
                "commission_amount": f"{total_commission:.2f}",
                "base_amount": f"{amount:.2f}",
                "settlement_status": "settled" if settled else RNG.choice(["pending", "canceled"]),
                "booked_at": ts(sold),
                "settled_at": ts(sold + dt.timedelta(days=30 * n)) if settled else "",
            })
    return sales, ledger


def gen_orders(rep_ids: list[str], customer_ids: list[str], products: list[dict]) -> list[dict]:
    """Build orders, including child orders and a few self-purchases.

    Both exist so the canonical count (root orders, excluding self-purchases) is genuinely
    different from the raw row count — the distinction orders_count vs all_orders_count exposes.

    Args:
        rep_ids (list[str]): Rep ids to draw from.
        customer_ids (list[str]): Customer ids to draw from.
        products (list[dict]): Product rows.

    Returns:
        list[dict]: The order rows.
    """
    rows = []
    root_ids: list[str] = []
    for i in range(N_ORDERS):
        product = RNG.choice(products)
        rep = RNG.choice(rep_ids)
        # ~12% are re-submissions of an existing order; ~3% are the rep buying for themselves.
        is_child = root_ids and RNG.random() < 0.12
        is_self = RNG.random() < 0.03
        order_id = f"ORD{i:06d}"
        if not is_child:
            root_ids.append(order_id)
        rows.append({
            "order_id": order_id,
            "parent_order_id": RNG.choice(root_ids) if is_child else "",
            "rep_id": rep,
            "customer_id": rep if is_self else RNG.choice(customer_ids),
            "product_id": product["product_id"],
            "product_line": product["product_line"],
            "assumed_value": money(400, 38000),
            "status_category": RNG.choice(["Aprovado", "Em análise", "Recusado", "Rascunho"]),
            "created_at": ts(rand_date()),
        })
    return rows


def gen_quotes(rep_ids: list[str], customer_ids: list[str], orders: list[dict]) -> list[dict]:
    """Build quotes, only some of which became an order.

    Args:
        rep_ids (list[str]): Rep ids to draw from.
        customer_ids (list[str]): Customer ids to draw from.
        orders (list[dict]): Orders, so a converted quote points at a real one.

    Returns:
        list[dict]: The quote rows.
    """
    return [{
        "quote_id": f"QUO{i:06d}",
        "rep_id": RNG.choice(rep_ids),
        "customer_id": RNG.choice(customer_ids),
        "product_line": RNG.choice(PRODUCT_LINES),
        "quoted_value": money(300, 40000),
        # A quote that never converted has no order — NULL is the outcome, not a gap.
        "order_id": RNG.choice(orders)["order_id"] if RNG.random() < 0.42 else "",
        "quoted_at": ts(rand_date()),
    } for i in range(N_QUOTES)]


def gen_funnel(quotes: list[dict]) -> list[dict]:
    """Build the chained journey, dropping off between stages.

    Args:
        quotes (list[dict]): Quotes that seed the top of the funnel.

    Returns:
        list[dict]: The journey rows.
    """
    rows = []
    for i, quote in enumerate(q for q in quotes if q["order_id"]):
        started = rand_date()
        approved = RNG.random() < 0.68
        sold = approved and RNG.random() < 0.55
        rows.append({
            "journey_id": f"JRN{i:06d}",
            "quote_id": quote["quote_id"],
            "order_id": quote["order_id"],
            "approved_order_id": quote["order_id"] if approved else "",
            "sale_order_id": quote["order_id"] if sold else "",
            "product_line": quote["product_line"],
            "cohort_month": month_start(started),
            "started_at": ts(started),
        })
    return rows


def gen_deals(line: str, rep_ids: list[str], customer_ids: list[str]) -> list[dict]:
    """Build one product line's deal pipeline.

    lost_reason is populated ONLY on a lost deal and closed_at ONLY once decided: the NULL
    semantics the model declares through meta.coverage.

    Args:
        line (str): The product line this pipeline belongs to.
        rep_ids (list[str]): Rep ids to draw from.
        customer_ids (list[str]): Customer ids to draw from.

    Returns:
        list[dict]: The deal rows, with that line's specific columns.
    """
    reasons = ["Preço", "Prazo de entrega", "Concorrência", "Crédito negado", "Sem resposta"]
    stages = ["Qualificação", "Proposta", "Negociação", "Fechamento"]
    rows = []
    for i in range(N_DEALS_PER_LINE):
        status = RNG.choices(["won", "lost", "open"], weights=[42, 33, 25])[0]
        created = rand_date(START, END - dt.timedelta(days=15))
        row = {
            "deal_id": f"{line[:3].upper()}D{i:05d}",
            "rep_id": RNG.choice(rep_ids),
            "customer_id": RNG.choice(customer_ids),
            "status": status,
            "stage_name": "Fechamento" if status != "open" else RNG.choice(stages),
            "lost_reason": RNG.choice(reasons) if status == "lost" else "",
            "deal_amount": money(800, 60000),
            "created_at": ts(created),
            "closed_at": "" if status == "open" else ts(
                created + dt.timedelta(days=RNG.randint(3, 120))),
        }
        if line == "Appliances":
            row["installation_required"] = RNG.random() < 0.45
            row["warranty_months"] = RNG.choice([12, 24, 36])
        elif line == "Furniture":
            row["is_custom_order"] = RNG.random() < 0.3
            row["lead_time_days"] = RNG.randint(5, 90)
        else:
            row["is_bundle"] = RNG.random() < 0.25
            row["trade_in_value"] = money(0, 4000)
        rows.append(row)
    return rows


def months_between(start: dt.date, end: dt.date) -> list[dt.date]:
    """List the first day of every month in a range.

    Args:
        start (dt.date): Range start.
        end (dt.date): Range end.

    Returns:
        list[dt.date]: One date per month.
    """
    out, cur = [], dt.date(start.year, start.month, 1)
    while cur <= end:
        out.append(cur)
        cur = dt.date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return out


def main() -> None:
    """Generate every table and write it to warehouse/seed/data/."""
    print(f"seed={SEED} (determinístico)")
    reps = gen_reps()
    customers = gen_customers()
    products = gen_products()
    rep_ids = [r["rep_id"] for r in reps]
    customer_ids = [c["customer_id"] for c in customers]
    months = months_between(START, END)

    write("core_rep.rep_overview", reps)
    write("core_customer.customer_overview", customers)
    write("core_product.product_overview", products)

    sales, ledger = gen_sales(rep_ids, customer_ids, products)
    write("core_sales.sales_summary", sales)
    write("core_sales.commission_ledger", ledger)

    orders = gen_orders(rep_ids, customer_ids, products)
    write("core_orders.orders_overview", orders)

    quotes = gen_quotes(rep_ids, customer_ids, orders)
    write("core_quotes.quotes_base", quotes)
    write("core_funnel.journey_funnel", gen_funnel(quotes))

    for line, mart in LINE_TO_MART.items():
        write(f"{mart}.deal_pipeline", gen_deals(line, rep_ids, customer_ids))
        write(f"{mart}.rep_ranking_monthly", [{
            "rep_id": rep,
            "reference_month": month_start(m),
            "production_amount": money(0, 250000),
            "production_score": RNG.randint(0, 1000),
            "rank_position": pos + 1,
            "quartile": ["Q1", "Q2", "Q3", "Q4"][min(pos * 4 // max(len(rep_ids), 1), 3)],
        } for m in months for pos, rep in enumerate(RNG.sample(rep_ids, 40))])

    # The abstract cube's table exists so the model compiles; it is never queried through a view.
    write("mart_pipeline.deal_pipeline_template", [])

    write("core_rep.portal_logins_monthly", [{
        "rep_id": rep, "reference_month": month_start(m),
        "logins_in_month": RNG.randint(0, 60), "active_days": RNG.randint(0, 28),
    } for m in months for rep in RNG.sample(rep_ids, 100)])

    leads = [{
        "lead_id": f"LEA{i:06d}",
        "rep_id": RNG.choice(rep_ids) if RNG.random() < 0.35 else "",
        "status": RNG.choices(["New", "Contacted", "Qualified", "Converted", "Rejected"],
                              weights=[25, 25, 20, 18, 12])[0],
        "channel": RNG.choice(["Direct", "Marketplace", "Inbound"]),
        "lead_score": RNG.randint(0, 100),
        "created_at": ts(rand_date()),
    } for i in range(N_LEADS)]
    for lead in leads:
        lead["converted_at"] = ts(rand_date()) if lead["status"] == "Converted" else ""
        lead["rejection_reason"] = (RNG.choice(["Perfil", "Documentação", "Desistência"])
                                    if lead["status"] == "Rejected" else "")
    write("core_rep.recruitment_leads", leads)

    write("core_rep.lead_status_changes", [{
        "change_id": f"CHG{i:06d}",
        "lead_id": RNG.choice(leads)["lead_id"],
        "from_status": RNG.choice(["New", "Contacted", "Qualified"]),
        "to_status": RNG.choice(["Contacted", "Qualified", "Converted", "Rejected"]),
        "changed_at": ts(rand_date()),
    } for i in range(N_LEADS * 2)])

    write("core_rep.rep_commercial_events", [{
        "event_id": f"EVT{i:06d}",
        "rep_id": RNG.choice(rep_ids),
        "customer_id": RNG.choice(customer_ids),
        "event_type": RNG.choice(["order", "sale"]),
        "product_line": RNG.choice(PRODUCT_LINES),
        "amount": money(300, 40000),
        "event_date": ts(rand_date()),
    } for i in range(8000)])

    write("mart_growth.rep_ramp_up", [{
        "rep_id": rep,
        "acquisition_channel": RNG.choice(["Direct", "Marketplace", "Inbound"]),
        "ramp_group": RNG.choice(["A", "B", "C"]),
        # Zero is written as a real 0 here; the mart that inspired this stored it as NULL, which is
        # the trap meta.coverage warns about on the growth view.
        "lifetime_orders": RNG.randint(0, 40),
        "lifetime_sales": RNG.randint(0, 25),
        "days_to_target": RNG.randint(10, 400) if RNG.random() < 0.6 else "",
        "first_order_at": ts(rand_date()) if RNG.random() < 0.8 else "",
        "first_sale_at": ts(rand_date()) if RNG.random() < 0.65 else "",
        "last_order_at": ts(rand_date()) if RNG.random() < 0.75 else "",
        "reached_target_at": ts(rand_date()) if RNG.random() < 0.5 else "",
    } for rep in rep_ids])

    charges = []
    for i in range(4000):
        rep = RNG.choice(rep_ids)
        comp = rand_date()
        status = RNG.choices(["paid", "confirmed", "overdue", "pending", "refunded", "voided"],
                             weights=[45, 15, 12, 15, 8, 5])[0]
        amount = float(money(89, 899))
        # One charge can appear on several invoice rows; is_primary_charge_row marks the one to
        # count, so a naive count of rows overstates the number of charges.
        for n in range(RNG.choice([1, 1, 1, 2])):
            charges.append({
                "id": f"CHG{i:06d}-{n}",
                "charge_id": f"CHG{i:06d}",
                "rep_id": rep,
                "plan": RNG.choice(["Annual", "Monthly"]),
                "charge_amount": f"{amount:.2f}",
                "charge_status": status,
                "payment_method": RNG.choice(["Boleto", "Cartão", "Pix"]),
                "competence_date": month_start(comp),
                "due_date": ts(comp + dt.timedelta(days=10)),
                "settled_at": ts(comp + dt.timedelta(days=RNG.randint(1, 40)))
                if status in ("paid", "confirmed") else "",
                "is_primary_charge_row": n == 0,
                "invoice_id": f"INV{i:06d}",
                "invoice_amount": f"{amount:.2f}",
                "invoice_status": RNG.choice(["authorized", "canceled", "pending"]),
                "payer_name": f"Rep {rep[-4:]}",
                "payer_tax_id": f"{RNG.randint(10**10, 10**11 - 1)}",
            })
    write("core_billing.subscription_charges_invoices", charges)

    write("core_billing.annual_renewal_cycles", [{
        "cycle_id": f"CYC{i:05d}",
        "rep_id": RNG.choice(rep_ids),
        "cycle_value": money(890, 4900),
        "commission_last_12m": money(0, 180000),
        "started_at": ts(rand_date(START, END - dt.timedelta(days=365))),
        "expected_renewal_at": ts(rand_date()),
        "renewal_status": RNG.choices(["renewed", "not_renewed"], weights=[62, 38])[0],
        "expiry_status": RNG.choice(["expired", "current"]),
        "renewed_at": ts(rand_date()) if RNG.random() < 0.62 else "",
    } for i in range(1500)])

    write("mart_suppliers.supplier_performance_monthly", [{
        "supplier_id": f"SUP{s:03d}",
        "supplier_name": f"Fornecedor {s:03d}",
        "country": RNG.choice(["Brasil", "China", "México", "Alemanha"]),
        "product_line": RNG.choice(PRODUCT_LINES),
        "reference_month": month_start(m),
        "total_deliveries": RNG.randint(1, 90),
        "delivered_on_time": RNG.randint(0, 80),
        "received_units": RNG.randint(50, 5000),
        "defective_units": RNG.randint(0, 120),
        "avg_lead_time_days": RNG.randint(3, 55),
        "purchase_amount": money(5000, 400000),
    } for m in months for s in range(1, N_SUPPLIERS + 1)])

    print(f"\nCSVs em {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
