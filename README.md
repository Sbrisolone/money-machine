# Money Machine — agente con strategia finanziaria integrata

Motore automatico (Python, zero dipendenze) che:
1. **Scopre** argomenti reali dai trend Google Italia + notizie Google News
2. **Genera** articoli SEO in italiano via Ollama (gratis) o upgrade premium
3. **Pubblica** automaticamente sul sito statico
4. **Gestisce il money** — la strategia che ha descritto:
   - Entrate → split automatico: X% SAFE (imbattibile), Y% RISK (budget rischio)
   - SAFE che supera la soglia → **auto-upgrade** paga da soli gli strumenti premium
   - RISK budget → campagne rischio:
     - se **guadagna** → % dei profitti riparte nel RISK (continuare), il resto → SAFE
     - se **perde** → è solo il budget rischio, il SAFE è imbattibile

## Installazione (2 minuti)

```bash
# 1) Ollama — LLM gratis e illimitato
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2

# 2) Site di base
python3 engine.py setup

# 3) Scopri argomenti reali
python3 engine.py discover --count 5

# 4) Genera articolo
python3 engine.py publish "viaggio low cost Italia"

# 5) Approva e pubblica
python3 engine.py review drafts/<file>.md

# 6) FULL AUTO — un articolo al giorno
python3 engine.py loop --interval 24 --max 30

# 7) Preview
python3 engine.py serve --port 8000
```

## La strategia finanziaria

```bash
# Registra un'entrata (AdSense, affiliate...)
python3 engine.py income 100 --source AdSense
# → €90 SAFE + €10 RISK (10% configurabile)

# Guarda lo stato
python3 engine.py finance

# Applica upgrade (se safe ≥ soglia, loop lo fa da soli)
python3 engine.py upgrade llm_premium

# Investe dal risk budget
python3 engine.py invest paid_ads 10 --note "Google Ads affiliate"

# Chiudi campagna
python3 engine.py settle paid_ads-20261024142636 25
# → profitto €15 → €7.50 riinvestito risk, €7.50 → safe
```

## Config (config.json)

```json
{
  "finance": {
    "risk_percentage": 10,
    "reinvest_percentage": 50,
    "auto_upgrades": true,
    "upgrades": {
      "llm_premium": {"cost": 20, "recurring": true, "threshold": 50,
        "desc": "Modello IA più potente → più traffico"},
      "custom_domain": {"cost": 12, "recurring": true, "threshold": 30,
        "desc": "Dominio .it — migliori admission AdSense"}
    },
    "risk_campaigns": {
      "paid_ads": {"enabled": true, "max_per_campaign": 20},
      "prediction_markets": {"enabled": false, "max_per_campaign": 25}
    }
  }
}
```

## La verità sul guadagno

- **€0 partenza**, agente fa fatica, tu supervisioni o `--auto`
- AdSense: approval 1-3 settimane dopo 15-20 articoli, €0.05-0.20/visualizzazione IT
- Affiliate: 5-15% su booking/voli
- **3-6 mesi** per primi risultati organici
- **Zero trading/crypto/posting social** — il sistema è pensato per soldi durevoli, non truffe virali

## Rischio mínimo

- Nessuna API a pagamento (fino a Ollama)
- Nessuna automation su social (ToS → ban)
- Contenuto su notizie reali (non spam AI)
- Risk budget con cap pro-campaign (il safe non tocca mai il rischio)
- Pagine AdSense obbligatorie generate automaticamente

## File

```
money-machine/
├── engine.py      # motore: discover/generate/publish/loop/serve + comandi finance
├── finance.py     # ledger: safe/risk split, upgrades, campagne rischio
├── config.json    # configurazione sito + strategia finanziaria
├── ledger.json    # storico movimenti finanziari (auto-generato)
├── site/          # sito statico (deploy gratis)
├── drafts/        # bozze in attesa di review
```
