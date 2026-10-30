# Guida: Money Machine su Android (Termux) — Gratis, Background 24/7

Il tuo Realme GT 5G può fare da server. Questa guida è passo-passo.

## Prima di iniziare

1. **Scarica Termux da F-Droid** (NON da Play Store — la versione Play Store è discontinua)
   - Apri: https://f-droid.org/packages/com.termux/
   - Installa e apri Termux

2. **Consigliato**: terminale ampio — usa un mouse/tastiera Bluetooth o una dock per mouse per comodità. Non è obbligatorio.

## Installazione (copia-incolla in Termux)

```bash
# 1. Aggiorna il sistema
pkg update && pkg upgrade -y

# 2. Installa Python, git, screen, curl
pkg install python git curl screen

# 3. Installa cloudflared (per esporre il sito pubblico — opzionale)
pkg install cloudflared

# 4. Accesso alla memoria del telefono
termux-setup-storage
```

## Recupera i file del Money Machine

**Opzione A — da GitHub (consigliata):**
1. Impegnare il repository money-machine su GitHub (vedi sotto)
2. In Termux:
```bash
git clone https://github.com/<NOME_UTENTE>/money-machine.git
cd money-machine
```

**Opzione B — da un file zip caricato sul telefono:**
1. Zippa la cartella `money-machine` sul computer
2. Trasferisci il zip sul telefono (via USB, Telegram, etc.)
3. In Termux:
```bash
cd ~/Downloads  # o dove è il file
unzip money-machine.zip
cd money-machine
```

## Configurazione

1. **Configura il sito** (nome, nicchia, affiliate):
   - Modifica `config.json` — vedi commenti dentro al file

2. **attiva la pubblicazione automatica** (per full-auto):
   - In `config.json` metti `"auto_publish": true`

3. **(Opzionale) API LLM gratuita** — per articoli veramente IA:
   - Register su https://huggingface.co →Settings → Access Tokens → crea token
   - In Termux:
```bash
export HF_TOKEN="il_tuo_token"
echo 'export HF_TOKEN="il_tuo_token"' >> ~/.bashrc
```

## Avvia il loop in background

```bash
# Modo full-auto: un articolo al giorno, in background
screen -S moneymachine python3 engine.py loop --interval 24 --max 100 --mode offline
```

**Se hai HF_TOKEN**, usa `--mode hf` invece di `--mode offline`.

**Come usare screen:**
- Aprire una sessione: `screen -S moneymachine`
- Eseguire il comando
- Uscire da screen premendo `Ctrl+A` poi `D` (detach — il processo continua)
- Riavviare screen: `screen -r moneymachine`

## Esporre il sito pubblicamente (opzionale, gratis)

```bash
# avvia il server del sito
python3 engine.py serve --port 8000

# in un'altra finestra di Termux, avvia il tunnel
cloudflared tunnel --url http://localhost:8000
```

Ti verrà data un URL pubblico come `https://parole-casuali.trycloudflare.com` — lo condividi e tutti possono vedere il blog.

## Mantenere il telefono in execução

1. **Sempre connesso al caricatore** — il loop è leggero ma il telefono non deve restare a terra
2. **Escludere Termux dal cleanup batterie**:
   - Impostazioni → App → Termux → Batteria → "Consenti attività in background"
   - Impostazioni → App → Termux → Avvio → "Avvia in background"
3. **Lasciare il telefono a napa onda** — il consumo è minimo (una fetch RSS al giorno)
4. **Se Android uccide Termux**:
   - Inserisci `~/.termux/boot/` e metteci `python3 /data/data/com.termux/files/home/money-machine/engine.py loop --interval 24 --max 100 --mode offline &`

## Guadagnare davvero

Il sistema fa:
1. ✅ Pubblicare articoli al giorno (tema reali dai trend Google)
2. ✅ Salvare i dati finanziari (safe/risk split, campagne)

Tu devi fare:
1. Approvare bozze: `python3 engine.py review drafts/<file>.md` (o full-auto con `auto_publish: true`)
2. **Iscriversi a AdSense** (dopo 15-20 articoli, con pagina About/Contact/Privacy già generate)
3. **Iscriversi agli affiliate** (Booking.com Partner Centre — link in config.json)
4. **Attendere** — 3-6 mesi per traffico organico

## Troubleshooting

| Problema | Soluzione |
|---|---|
| Termux non parte da boot | Inserire script in `~/.termux/boot/` |
| Articles non si generano | Controllare `drafts/` — se vuoto, il loop ha problemi |
| Battery si esaurisce | Togliere `--max 100`, usare `--interval 48` (1 articolo per 2 giorni) |
| cloudflared non dà URL | Riprovare — i tunnel gratuiti sono temporanei |
| "port already in use" | Altro processo usa 8000 — cambia con `--port 8001` |

## FAQ

**Q: Quanto consuma batterya?**
A: Il loop fa fetch RSS (10 KB al giorno) + generazione offline. Consumo minimo con screen off.

**Q: Il telefono si surriscalda?**
A: No — il carico è leggero. Solo se usi Ollama sul telefono (consigliato di no).

**Q: Si può usare il telefono anche normalmente?**
A: Sì, ma se l'app del loop è in esecuzione, la batteria si esaurisce più in fretta. Meglio un telefono dedicato.

**Q: E se ho un telefono più vecchio?**
A: Funziona anche con 4GB RAM — il loop è leggero.
