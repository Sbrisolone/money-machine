#!/usr/bin/env python3
"""
Finanza del Money Machine — safe, risk budget, upgrades, campagne rischio.

Principio (come descrivevi tu):
  - ogni entrata viene splittata: X% → SAFE (imbattibile), Y% → RISK (budget rischio)
  - quando il SAFE supera una soglia, si spende da soli per upgrade pagati (LLM premium, dominio...)
  - dal RISK si investe in campagne; se perde, è solo quella parte; se guadagna,
    una % dei profitti riparte nel RISK (continuare), il resto va nel SAFE.

Avvertimento: la modalità "prediction markets" (Polymarket etc.) è AZZAREDO a
sspettativa negativa — è disattivata per default. Il default è rischio business
(annunci pagati per affiliate), che è un investimento, non un gioco.
"""

import datetime as dt
import json
import os

LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.json")

DEFAULT_LEDGER = {
    "safe_balance": 0.0,
    "risk_balance": 0.0,
    "total_earned": 0.0,
    "total_safe": 0.0,
    "total_risk": 0.0,
    "total_invested": 0.0,
    "history": [],
    "campaigns": {},
    "upgrades_applied": [],
}


def load_ledger():
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH) as f:
            data = json.load(f)
    else:
        data = {}
    for k, v in DEFAULT_LEDGER.items():
        if k not in data:
            data[k] = v.copy() if isinstance(v, list) else (dict(v) if isinstance(v, dict) else v)
    return data


def save_ledger(ledger):
    with open(LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _entry(ledger, type_, amount, note):
    ledger["history"].append({
        "ts": _now(), "type": type_, "amount": round(float(amount), 2), "note": note,
    })


# ============================================================
# EARNING — split entry
# ============================================================

def report_earning(ledger, cfg, amount, source):
    fin = cfg.get("finance", {})
    risk_pct = float(fin.get("risk_percentage", 10)) / 100
    amount = float(amount)
    risk_amt = round(amount * risk_pct, 2)
    safe_amt = round(amount - risk_amt, 2)
    ledger["safe_balance"] = round(ledger["safe_balance"] + safe_amt, 2)
    ledger["risk_balance"] = round(ledger["risk_balance"] + risk_amt, 2)
    ledger["total_earned"] = round(ledger["total_earned"] + amount, 2)
    ledger["total_safe"] = round(ledger["total_safe"] + safe_amt, 2)
    ledger["total_risk"] = round(ledger["total_risk"] + risk_amt, 2)
    _entry(ledger, "income", amount, f"{source} — split {safe_amt} safe / {risk_amt} risk")
    # paga upgrades ricorrenti (es. LLM premium) dal safe prima della split
    auto_upgrade_check(ledger, cfg, silent=False)
    save_ledger(ledger)
    return safe_amt, risk_amt


# ============================================================
# UPGRADES — spend del safe per strumenti pagati
# ============================================================

def list_upgrades(cfg):
    return cfg.get("finance", {}).get("upgrades", {})


def apply_upgrade(ledger, cfg, name):
    up = list_upgrades(cfg).get(name)
    if not up:
        return False, f"upgrade '{name}' non trovato. Disponibili: {list(list_upgrades(cfg))}"
    if name in ledger["upgrades_applied"]:
        return False, f"'{name}' già applicato"
    cost = float(up.get("cost", 0))
    if ledger["safe_balance"] < cost:
        return False, f"safe {ledger['safe_balance']:.2f} < costo {cost:.2f}"
    ledger["safe_balance"] = round(ledger["safe_balance"] - cost, 2)
    ledger["upgrades_applied"].append(name)
    _entry(ledger, "upgrade", cost, f"{up.get('name', name)} — {up.get('desc', '')}")
    save_ledger(ledger)
    return True, f"✔ upgrade applicato: {up.get('name', name)} (−{cost:.2f})"


def auto_upgrade_check(ledger, cfg, silent=False):
    """Applica upgrades disponibili quando il safe supera la soglia."""
    if not cfg.get("finance", {}).get("auto_upgrades", True):
        return []
    applied = []
    for name, up in list_upgrades(cfg).items():
        if name in ledger["upgrades_applied"]:
            continue
        # paga recurrenti da soli se il costo è coperto
        cost = float(up.get("cost", 0))
        threshold = float(up.get("threshold", cost))
        if ledger["safe_balance"] >= threshold:
            ok, msg = apply_upgrade(ledger, cfg, name)
            if ok and not silent:
                log(msg)
            if ok:
                applied.append(name)
    return applied


def pay_recurring(ledger, cfg):
    """Paga mensilmente i upgrades ricorrenti (chiamato dall'income)."""
    for name in ledger["upgrades_applied"]:
        up = list_upgrades(cfg).get(name, {})
        if up.get("recurring"):
            cost = float(up.get("cost", 0))
            if ledger["safe_balance"] >= cost:
                ledger["safe_balance"] = round(ledger["safe_balance"] - cost, 2)
                _entry(ledger, "recurring", cost, f"rinnovo {up.get('name', name)}")
                save_ledger(ledger)


# ============================================================
# RISK CAMPAIGN — invest dal risk budget
# ============================================================

def risk_campaigns(cfg):
    return cfg.get("finance", {}).get("risk_campaigns", {})


def invest(ledger, cfg, campaign, amount, note=""):
    campaigns = risk_campaigns(cfg)
    if campaign not in campaigns:
        return False, f"campagna '{campaign}' non definita. Disponibili: {list(campaigns)}"
    spec = campaigns[campaign]
    if not spec.get("enabled", False):
        return False, f"campagna '{campaign}' disattivata in config (è gambling o non sicurissima)"
    max_per = float(spec.get("max_per_campaign", 20))
    amount = float(amount)
    if amount <= 0:
        return False, "importo invalido"
    if amount > ledger["risk_balance"]:
        return False, f"risk budget {ledger['risk_balance']:.2f} < {amount:.2f}"
    if amount > max_per:
        return False, f"supera max_per_campaign ({max_per}) — budget sotto controllo"
    ledger["risk_balance"] = round(ledger["risk_balance"] - amount, 2)
    ledger["total_invested"] = round(ledger["total_invested"] + amount, 2)
    cid = f"{campaign}-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
    ledger["campaigns"][cid] = {
        "campaign": campaign, "invested": amount, "note": note,
        "status": "open", "created": _now(),
    }
    _entry(ledger, "invest", amount, f"{campaign} ({cid}) {note}")
    save_ledger(ledger)
    return True, f"✔ investito {amount:.2f} in {campaign} (id: {cid})"


def settle(ledger, cfg, campaign_id, return_amount):
    if campaign_id not in ledger["campaigns"]:
        return False, f"campagna {campaign_id} non trovata"
    camp = ledger["campaigns"][campaign_id]
    if camp["status"] != "open":
        return False, f"campagna già chiusa ({camp['status']})"
    invested = float(camp["invested"])
    ret = float(return_amount)
    profit = round(ret - invested, 2)
    reinvest_pct = float(cfg.get("finance", {}).get("reinvest_percentage", 50)) / 100
    camp["status"] = "closed"
    camp["return"] = ret
    camp["profit"] = profit
    if profit > 0:
        reinvest = round(profit * reinvest_pct, 2)
        to_safe = round(profit - reinvest, 2)
        ledger["risk_balance"] = round(ledger["risk_balance"] + reinvest, 2)
        ledger["safe_balance"] = round(ledger["safe_balance"] + to_safe, 2)
        note = f"{campaign_id}: profitto {profit:.2f} — {reinvest:.2f} riinvestito risk, {to_safe:.2f} → safe"
    elif profit < 0:
        note = f"{campaign_id}: perdita {abs(profit):.2f} — coperta solo dal risk budget (safe ileso)"
    else:
        note = f"{campaign_id}: nessun rendimiento ({ret:.2f})"
    _entry(ledger, "settle", ret, note)
    save_ledger(ledger)
    return True, note


def status(ledger, cfg):
    fin = cfg.get("finance", {})
    lines = [
        f"═══ Status finanziario ═══",
        f"SAFE (imbattibile):     {ledger['safe_balance']:.2f}€",
        f"RISK budget:           {ledger['risk_balance']:.2f}€",
        f"Totale guadagnato:     {ledger['total_earned']:.2f}€",
        f"Storia:                 {len(ledger['history'])} movimenti",
        "",
        "Upgrades:",
    ]
    applied = ledger["upgrades_applied"]
    if applied:
        for name in applied:
            up = list_upgrades(cfg).get(name, {})
            lines.append(f"  ✔ {up.get('name', name)} — {up.get('desc', '')}")
    else:
        lines.append("  (nessuno — il safe non ha superato le soglie)")
    lines.append("")
    lines.append("Campagne rischio:")
    open_camps = {k: v for k, v in ledger["campaigns"].items() if v["status"] == "open"}
    if open_camps:
        for cid, c in open_camps.items():
            lines.append(f"  ● {cid}: {c['campaign']} — investito {c['invested']:.2f}€")
    else:
        lines.append("  (nessuna campaign aperta)")
    return "\n".join(lines)


def log(msg):
    print(f"[money-machine] {msg}")


if __name__ == "__main__":
    cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")))
    led = load_ledger()
    print(status(led, cfg))
