#!/usr/bin/env python3
"""
Money Machine — motore automatico per creare e pubblicare articoli monetizzabili.

Pipeline:
  1. DISCOVER  — topic reali dai trend Google (IT) e notizie Google News
  2. GENERATE — articolo SEO completo (Italiano) via Ollama (gratis/illimitato)
  3. REVIEW    — bozza per approvazione umana (default) o --auto per piena autonomia
  4. PUBLISH   — rendering HTML statico + pubblicazione sul sito
  5. LOOP      — modalità continua (--loop --interval 24h)

Rischio minimo:
  - Niente posting su Instagram/TikTok/YouTube (viola ToS → ban)
  - Niente trading/crypto (gioco)
  - Contenuto basato su notizie reali (non spam AI)
  - Pagine AdSense obbligatorie generate automatically
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import finance as fin

WORKDIR = os.path.dirname(os.path.abspath(__file__))
SITEDIR = os.path.join(WORKDIR, "site")
POSTSDIR = os.path.join(SITEDIR, "posts")
DRAFTSDIR = os.path.join(WORKDIR, "drafts")
CONFIGPATH = os.path.join(WORKDIR, "config.json")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
HF_API = "https://api-inference.huggingface.co/models"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

NS_TRENDS = {"ht": "https://trends.google.com/trending/rss"}


# ============================================================
# CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "niche": "viaggi low cost Italia",
    "site_name": "Money Machine Blog",
    "language": "it",
    "geo": "IT",
    "ollama_model": "llama3.2",
    "min_words": 800,
    "auto_publish": False,
    "adsense_client": "",           # es. ca-pub-XXXXXXXXXXXXXXXX
    "affiliate_links": {},          # {"descrizione": "https://link.affiliate"}
    "author": {"name": "Redazione", "bio": "Guide pratiche su viaggi low cost e risparmio."}
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    cfg["author"] = dict(DEFAULT_CONFIG["author"])
    cfg["affiliate_links"] = dict(DEFAULT_CONFIG["affiliate_links"])
    if os.path.exists(CONFIGPATH):
        with open(CONFIGPATH) as f:
            user_cfg = json.load(f)
        cfg.update({k: v for k, v in user_cfg.items() if k not in ("author", "affiliate_links")})
        cfg["author"].update(user_cfg.get("author", {}))
        cfg["affiliate_links"].update(user_cfg.get("affiliate_links", {}))
    return cfg


def save_config(cfg):
    with open(CONFIGPATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ============================================================
# DISCOVER — trend + notizie (fonti gratuite, reali)
# ============================================================

def _fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_trends(geo="IT", n=10):
    """Trend Google del giorno per paese."""
    xml_data = _fetch(f"https://trends.google.com/trending/rss?geo={geo}")
    root = ET.fromstring(xml_data)
    out = []
    for rank, item in enumerate(root.iter("item"), start=1):
        news = []
        for nitem in item.findall("ht:news_item", NS_TRENDS):
            news.append({
                "title": nitem.findtext("ht:news_item_title", namespaces=NS_TRENDS),
                "url": nitem.findtext("ht:news_item_url", namespaces=NS_TRENDS),
                "source": nitem.findtext("ht:news_item_source", namespaces=NS_TRENDS),
            })
        out.append({
            "rank": rank,
            "query": item.findtext("title"),
            "traffic": item.findtext("ht:approx_traffic", namespaces=NS_TRENDS),
            "news": [n for n in news if n["title"]],
        })
        if len(out) >= n:
            break
    return out


def fetch_news(query, hl="it", gl="IT", ceid="IT:it", n=10):
    """Articoli reali su un tema."""
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
    root = ET.fromstring(_fetch(url))
    out = []
    for item in root.iter("item"):
        title = item.findtext("title")
        source_el = item.find("source")
        source = source_el.text if source_el is not None else ""
        if not title:
            continue
        out.append({"title": title, "url": item.findtext("link"), "source": source})
        if len(out) >= n:
            break
    return out


def discover_topics(cfg, count=5):
    """Combina trend di nicchia + news."""
    topics = []
    # 1) trend del giorno
    try:
        for t in fetch_trends(cfg["geo"]):
            if t["news"]:
                topics.append({
                    "type": "trend",
                    "topic": t["query"],
                    "traffic": t["traffic"],
                    "news": t["news"],
                })
    except Exception as e:
        log(f"trend feed fallito: {e}")
    # 2) news di nicchia
    try:
        news = fetch_news(cfg["niche"])
        if news:
            topics.append({
                "type": "niche",
                "topic": cfg["niche"],
                "news": news,
            })
    except Exception as e:
        log(f"news feed fallito: {e}")
    # 3) news su trend rilevanti (argomenti già notizia)
    enriched = []
    for t in topics[:count]:
        if t["type"] == "trend" and t["news"]:
            first_news = t["news"][0]
            try:
                more = fetch_news(first_news["title"][:60], n=6)
                if more:
                    t["news"] = more + t["news"][:3]
            except Exception:
                pass
        enriched.append(t)
    return enriched


# ============================================================
# GENERATE — articolo via Ollama / HF / offline
# ============================================================

def ollama_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return []


def call_ollama(model, prompt):
    data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"]


def call_hf(model, prompt):
    data = json.dumps({"inputs": prompt}).encode()
    req = urllib.request.Request(
        f"{HF_API}/{model}", data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {HF_TOKEN}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read())
    if isinstance(out, list):
        return out[0].get("generated_text", "")
    return out.get("generated_text", "")


def resolve_provider(mode):
    if mode == "ollama":
        if not ollama_models():
            raise SystemExit("Ollama non rilevato. Installa: "
                             "curl -fsSL https://ollama.com/install.sh | sh "
                             "poi: ollama pull llama3.2")
        return "ollama", "llama3.2"
    if mode == "hf":
        if not HF_TOKEN:
            raise SystemExit("Imposta HF_TOKEN (token gratuito su huggingface.com)")
        return "hf", "HuggingFaceTB/SmolLM2-360M-Instruct"
    if ollama_models():
        return "ollama", "llama3.2"
    if HF_TOKEN:
        return "hf", "HuggingFaceTB/SmolLM2-360M-Instruct"
    return "offline", None


def build_prompt(cfg, topic, news_items):
    """Prompt per articolo SEO basato su notizie reali."""
    news_lines = []
    for i, n in enumerate(news_items[:8], start=1):
        news_lines.append(f"{i}. {n['title']} (fonte: {n.get('source', 'news')})")
    sources = "\n".join(news_lines)

    affi = cfg["affiliate_links"]
    affi_block = ""
    if affi:
        lines = ["Avecto finale (call to action) che incrocia questi link:"]
        for desc, link in affi.items():
            lines.append(f"- {desc}: {link}")
        affi_block = "\n".join(lines)

    return f"""Scrivi un articolo SEO completo in italiano ({cfg['min_words']}+ parole) su: "{topic}"

Fatti e fonti disponibili (da usare come base reale, NON inventare dati):
{sources}

Struttura richiesta (markdown):
# {topic}: <titolo accattivante ma non clickbait>

<paragrafo di ingresso 60-80 parole, stringa la domanda del lettore>

## <sottotitolo H2>
...contenuto 3-4 paragrafi...

## <sottotitolo H2>
...

## Domande frequenti (FAQ)
**Domanda?**
Risposta breve.

## Conclusione
...

**Fonti:**
- {"- ".join(n['title'] for n in news_items[:5])}

{affi_block}

Regole:
- Italiano nativo, tono editoriale di un sito utilitário
- Fatti basati solo sulle fonti fornite, NON inventare numeri/statistiche
- scrivere come redazione umana competente, con E-E-A-T
- 800-1100 parole
- niente "dato che sono un AI", niente spazzatura
"""


def offline_article(cfg, topic, news_items):
    """Fallback offline: articolo strutturato da notizie reali."""
    lines = [f"# {topic}: tutto ciò che c'è da sapere", ""]
    if news_items:
        # ingresso da prima notizia
        lines.append(f"**In sintesi.** {news_items[0]['title']} — "
                     f"{news_items[0].get('source', 'fonte').lower()}. "
                     f"Analizziamo il tema punto per punto.")
        lines.append("")
    # body: 3 H2 da notizie
    seen = set()
    body = []
    for n in news_items[:6]:
        t = n["title"].rstrip(".")
        if t in seen:
            continue
        seen.add(t)
        body.append(f"## {t}\n\n{n.get('source', 'fonte').lower()} ha riportato: "
                    f"\"{n['title']}\". Si tratta di un tema in evoluzione: "
                    "approcfondi i dettagli in questa sezione, con i dati a disposizione "
                    "e i link alle fonti. Aggiornamento continuo."
                    + f"\n\n[Leggi la fonte originale]({n['url']})\n")
    if body:
        lines.extend(body)
    lines.append("## Conclusione\n\n"
                 "In sintesi, il tema si evolve rapidamente e le fonti aggiornate sono "
                 "essenziali per capire la situazione. Continua a seguire gli aggiornamenti.\n")
    lines.append("**Fonti:**\n")
    for n in news_items[:5]:
        lines.append(f"- {n['title']} — {n.get('source', 'fonte')}")
    lines.append("")
    lines.append(f"*Articolo a cura della redazione {cfg['author']['name']}.*")
    return "\n".join(lines)


def generate_article(cfg, topic, news_items, mode="auto"):
    provider, model = resolve_provider(mode)
    if provider == "offline":
        log("modalità offline (template da notizie reali)")
        return offline_article(cfg, topic, news_items)
    log(f"generazione via {provider} ({model})...")
    prompt = build_prompt(cfg, topic, news_items)
    try:
        if provider == "ollama":
            return call_ollama(model, prompt)
        return call_hf(model, prompt)
    except Exception as e:
        log(f"generazione fallita ({e}), uso fallback offline")
        return offline_article(cfg, topic, news_items)


# ============================================================
# RENDER — markdown → HTML
# ============================================================

def md_to_html(md):
    """Convertitore minimale markdown → HTML (solo stdlib)."""
    out = []
    in_list = False
    for line in md.split("\n"):
        stripped = line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{html.escape(m.group(2))}</h{level}>")
            continue
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            out.append(f"<p><strong>{html.escape(stripped[2:-2])}</strong></p>")
            continue
        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline_md(stripped[2:])}</li>")
            continue
        if re.match(r"^\d+\.\s", stripped):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline_md(re.sub(r'^\\d+\\.\\s', '', stripped))}</li>")
            continue
        out.append(f"<p>{inline_md(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def inline_md(text):
    # link [testo](url)
    def link_repl(m):
        return f'<a href="{html.escape(m.group(2))}" rel="nofollow">{html.escape(m.group(1))}</a>'
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", link_repl, text)
    # corsivo *testo*
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    # **bold**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def render_page(cfg, title, content_html, slug=""):
    adsense = ""
    if cfg.get("adsense_client"):
        adsense = (
            '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
            + cfg["adsense_client"] + '" crossorigin="anonymous"></script>')
    affi = cfg["affiliate_links"]
    affi_block = ""
    if affi:
        rows = "".join(f'<li><a href="{html.escape(l)}" rel="sponsored nofollow" target="_blank">{html.escape(d)}</a></li>'
                       for d, l in affi.items())
        affi_block = f"<h3>Risorse utili</h3><ul>{rows}</ul>"
    nav = f'<a href="/">{html.escape(cfg["site_name"])}</a> · <a href="/about.html">Chi siamo</a> · <a href="/contact.html">Contatti</a> · <a href="/privacy.html">Privacy</a>'
    author = cfg.get("author", {})
    body = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · {html.escape(cfg['site_name'])}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:2rem 1.2rem;line-height:1.7;color:#222}}
nav{{padding:1rem 0;border-bottom:1px solid #eee;margin-bottom:2rem;font-size:.95rem}}
a{{color:#0366d6;text-decoration:none}}
h1,h2{{line-height:1.3}}
img{{max-width:100%}}
blockquote{{border-left:4px solid #ddd;margin-left:0;padding-left:1rem;color:#555}}
.author{{margin-top:3rem;padding-top:1rem;border-top:1px solid #eee;font-size:.9rem;color:#666}}
.affi{{background:#f6f8fa;padding:1rem 1.2rem;border-radius:8px;margin-top:2rem}}
</style>
{adsense}
</head>
<body>
<nav>{nav}</nav>
<article>
{content_html}
{affi_block and f'<div class="affi">{affi_block}</div>'}
<div class="author">
<p><strong>{html.escape(author.get('name', 'Redazione'))}</strong> — {html.escape(author.get('bio', ''))}</p>
</div>
</article>
</body>
</html>"""
    return body


# ============================================================
# PUBLISH
# ============================================================

def slugify(text):
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)[:60]
    return slug or "post"


def publish_article(cfg, title, markdown, topic, auto=False):
    os.makedirs(POSTSDIR, exist_ok=True)
    os.makedirs(DRAFTSDIR, exist_ok=True)
    slug = slugify(title)
    path = os.path.join(POSTSDIR, slug + ".html")
    content_html = md_to_html(markdown)
    page = render_page(cfg, title, content_html, slug)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    # draft di riferimento
    draft = os.path.join(DRAFTSDIR, slug + ".md")
    with open(draft, "w", encoding="utf-8") as f:
        f.write(markdown)
    log(f"articolo pubblicato: {slug}.html")
    log(f"bozza markdown: {draft}")
    return path, draft


def publish_topic(cfg, topic, mode, auto):
    news = topic.get("news", [])
    markdown = generate_article(cfg, topic["topic"], news, mode)
    # titolo = primo H1 o topic
    m = re.match(r"^#\s+(.*)", markdown)
    title = m.group(1).strip() if m else topic["topic"]
    # rimuovi h1 dal body (gia' nel title)
    body = re.sub(r"^#\s+.*\n+", "", markdown)
    if auto or cfg.get("auto_publish"):
        path, draft = publish_article(cfg, title, body, topic["topic"])
        log(f"✔ AUTO-PUBLISH: {title}")
        return path
    # default: salva bozza in attesa di review
    os.makedirs(DRAFTSDIR, exist_ok=True)
    slug = slugify(title)
    draft = os.path.join(DRAFTSDIR, slug + ".md")
    with open(draft, "w", encoding="utf-8") as f:
        f.write(markdown)
    log(f"bozza salvata per review: {draft}")
    log("approva la bozza con: python3 engine.py review <file.md>")
    return draft


def run_review(path):
    """Revisione + pubblicazione di una bozza."""
    with open(path) as f:
        md = f.read()
    cfg = load_config()
    m = re.match(r"^#\s+(.*)", md)
    title = m.group(1).strip() if m else os.path.basename(path)
    body = re.sub(r"^#\s+.*\n+", "", md)
    path_pub, _ = publish_article(cfg, title, body, title)
    log(f"✔ pubblicato: {title}")
    return path_pub


# ============================================================
# SITE — setup statico (pagine AdSense obbligatorie)
# ============================================================

def setup_site(cfg, force=False):
    os.makedirs(SITEDIR, exist_ok=True)
    if not force and os.path.exists(os.path.join(SITEDIR, "index.html")):
        log("sito già presente. --force per ricreare")
        return
    author = cfg.get("author", {})
    adsense = ""
    if cfg.get("adsense_client"):
        adsense = ('<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
                   + cfg["adsense_client"] + '" crossorigin="anonymous"></script>')
    nav = f'<a href="/">{html.escape(cfg["site_name"])}</a> · <a href="/about.html">Chi siamo</a> · <a href="/contact.html">Contatti</a> · <a href="/privacy.html">Privacy</a>'
    styles = """body{font-family:system-ui,-apple-system,sans-serif;max-width:720px;margin:0 auto;padding:2rem 1.2rem;line-height:1.7;color:#222}
nav{padding:1rem 0;border-bottom:1px solid #eee;margin-bottom:2rem}
a{color:#0366d6;text-decoration:none}
h1,h2{line-height:1.3}"""

    index_tpl = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__SITE__</title><style>__STYLES__</style>__ADS__</head>
<body><nav>__NAV__</nav>
<h1>__SITE__</h1>
<p>Articoli su <strong>__NICHE__</strong>. Aggiornamento automatico.</p>
<h2>Articoli recenti</h2>
<div id="posts">Caricamento articoli...</div>
<script>
fetch('/posts.json').then(r=>r.ok?r.json():[]).then(posts=>{
  const d=document.getElementById('posts');
  if(!posts.length){d.textContent='Nessun articolo ancora — esegui: python3 engine.py publish <topic>';return}
  d.innerHTML=posts.map(function(p){
    return '<article style="margin-bottom:2rem"><h3><a href="/posts/' + p.slug + '.html">' + p.title + '</a></h3><p style="color:#666;font-size:.9rem">' + p.date + '</p></article>';
  }).join('');
});
</script></body></html>"""
    index = (index_tpl
             .replace("__SITE__", html.escape(cfg["site_name"]))
             .replace("__STYLES__", styles)
             .replace("__ADS__", adsense)
             .replace("__NAV__", nav)
             .replace("__NICHE__", html.escape(cfg["niche"])))
    with open(os.path.join(SITEDIR, "index.html"), "w") as f:
        f.write(index)

    about = f"""<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Chi siamo · {html.escape(cfg["site_name"])}</title>
<style>{styles}</style></head><body><nav>{nav}</nav>
<h1>Chi siamo</h1>
<p>{html.escape(cfg["site_name"])} è un sito dedicato a <strong>{html.escape(cfg["niche"])}</strong>.</p>
<p><strong>{html.escape(author.get("name", "Redazione"))}</strong> — {html.escape(author.get("bio", ""))}</p>
<p>I contenuti sono redatti con cura e aggiornati costantemente. Per domande o proposte:
<a href="/contact.html">contattaci</a>.</p></body></html>"""
    with open(os.path.join(SITEDIR, "about.html"), "w") as f:
        f.write(about)

    contact = f"""<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Contatti · {html.escape(cfg["site_name"])}</title>
<style>{styles}</style></head><body><nav>{nav}</nav>
<h1>Contatti</h1>
<p>Scrivici per segnalare errori, proporre argomenti o collaborare.</p>
<ul>
<li>Email: <a href="mailto:redazione@example.com">redazione@example.com</a></li>
<li>Social: [inserire i propri]</li>
</ul></body></html>"""
    with open(os.path.join(SITEDIR, "contact.html"), "w") as f:
        f.write(contact)

    privacy = """<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Policy · TITLE</title><style>""" + styles + """</style></head><body><nav>NAV</nav>
<h1>Privacy Policy</h1>
<p>Nella misura in cui l'operatore del sito ha interesse a servirsi di cookie, queste informazioni verranno utilizzate soltanto per permettere il funzionamento del sito.</p>
<p>I visitatori possono rifiutare di fornire una identità personale (ad esempio nominativo e indirizzo e-mail), ma potrebbe non essere possibile svolgere alcune funzioni del sito.</p>
<p>Questo sito utilizza eventualmente cookie per il funzionamento del servizio e per migliorare l'esperienza dei visitatori. L'utente può rifiutarsi di utilizarli, ma potrebbero verificarsi conseguenze funzionali.</p>
<p>Eventuali dati personali raccolti verranno utilizzati soltanto per finalità strettamente connesse all'uso del sito.</p>
<p>Per informazioni circa le privacy e i cookie, puoi visitare la pagina appropriéa del sito o richiedere informazioni direttamente agli amministratori.</p></body></html>"""
    privacy = privacy.replace("TITLE", html.escape(cfg["site_name"])).replace("NAV", nav)
    with open(os.path.join(SITEDIR, "privacy.html"), "w") as f:
        f.write(privacy)
    log(f"sito creato: {SITEDIR} (index, about, contact, privacy)")


def save_posts_index():
    """Genera posts.json per l'homepage."""
    posts = []
    if os.path.isdir(POSTSDIR):
        for fname in sorted(os.listdir(POSTSDIR), reverse=True):
            if not fname.endswith(".html"):
                continue
            with open(os.path.join(POSTSDIR, fname)) as f:
                content = f.read()
            m = re.search(r"<title>(.*?)(?: ·|</title>)", content)
            title = html.unescape(m.group(1)) if m else fname
            slug = fname[:-5]
            d = re.search(r"datetime=\"([^\"]+)\"", content)
            date = d.group(1)[:10] if d else ""
            posts.append({"slug": slug, "title": title, "date": date})
    with open(os.path.join(SITEDIR, "posts.json"), "w") as f:
        json.dump(posts, f, indent=2, ensure_ascii=False)


# ============================================================
# DASHBOARD — export statico
# ============================================================

def export_dashboard(cfg):
    """Genera dashboard statico (dati incorporati, niente API) in site/dashboard.html."""
    led = fin.load_ledger()
    posts = _count_posts()
    total = led["total_earned"]
    safe = led["safe_balance"]
    risk = led["risk_balance"]
    upgrades = led["upgrades_applied"]
    upgrades_avail = fin.list_upgrades(cfg)
    campaigns = led["campaigns"]
    history = led["history"]
    tips = cfg.get("tips", [])

    # timeline by month (last 6)
    by_month = {}
    for h in history:
        if h["type"] != "income":
            continue
        d = (h.get("ts") or "")[:7]
        if d:
            by_month[d] = round(by_month.get(d, 0) + h["amount"], 2)
    months = sorted(by_month.keys())[-6:]
    max_month = max([by_month[m] for m in months] + [1])

    safe_pct = round((safe / (safe + risk)) * 100) if (safe + risk) > 0 else 100
    risk_pct = 100 - safe_pct

    def fmt(n):
        return "€" + f"{n or 0:.2f}".replace(".", ",")

    # campaigns rows
    camp_rows = []
    for cid, c in campaigns.items():
        profit = round(c.get("return", 0) - c["invested"], 2) if c.get("return") is not None else None
        camp_rows.append(
            f"<tr><td>{cid}</td><td>{c['campaign']}</td><td>{fmt(c['invested'])}</td>"
            f"<td>{fmt(c.get('return')) if c.get('return') is not None else '—'}</td>"
            f"<td class=\"{'profit' if profit and profit > 0 else 'loss' if profit and profit < 0 else ''}\">"
            f"{'+' if profit and profit > 0 else ''}{profit if profit is not None else '—'}</td>"
            f"<td><span class=\"status-pill {c['status'] == 'open' and 'status-open' or 'status-closed'}\">"
            f"{c['status'] == 'open' and 'aperta' or 'chiusa'}</span></td></tr>"
        )

    up_chips = "".join(
        f"<span class=\"upgrade-chip\" style=\"{'background:#e8f5e9;color:#0a9e5e' if name in upgrades else ''}\">"
        f"{'✔ ' if name in upgrades else ''}{u['name']} — {u['desc']}</span>"
        for name, u in upgrades_avail.items()
    ) or '<p class="muted">Nessun upgrade configurato</p>'

    tips_html = "".join(f"<div class=\"tip\">{t}</div>" for t in tips)

    bars_html = ""
    if months:
        for m in months:
            h = round((by_month[m] / max_month) * 100)
            bars_html += (f"<div class=\"bar-col\"><div class=\"bar\" style=\"height:{h}%\"></div>"
                          f"<div class=\"bar-label\">{m[5:]}</div></div>")
    else:
        bars_html = "<p class=\"muted\">Nessuna entrata registrata</p>"

    camp_rows_html = "".join(camp_rows) or '<tr><td colspan="6" class="muted">Nessuna campagna</td></tr>'

    html = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Money Machine — Dashboard</title>
<style>
:root { --green:#0a9e5e; --orange:#f08a24; --blue:#0366d6; --bg:#f5f7fa; --text:#222; --muted:#666; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }
header { background: linear-gradient(135deg, #0a9e5e 0%, #0366d6 100%); color: #fff; padding: 1.5rem 1.2rem; }
header h1 { font-size: 1.4rem; }
header p { font-size: .9rem; opacity: .9; }
main { max-width: 960px; margin: 1.2rem auto; padding: 0 1rem 3rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
.card { background: #fff; border-radius: 12px; padding: 1.2rem; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.card .label { font-size: .8rem; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.card .value { font-size: 1.8rem; font-weight: 700; margin-top: .3rem; }
.card .sub { font-size: .78rem; color: var(--muted); margin-top: .2rem; }
.card.green .value { color: var(--green); }
.card.orange .value { color: var(--orange); }
.card.blue .value { color: var(--blue); }
.panel { background: #fff; border-radius: 12px; padding: 1.2rem; margin-bottom: 1.2rem; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
.panel h2 { font-size: 1.05rem; margin-bottom: .9rem; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
@media (max-width: 720px) { .two-col { grid-template-columns: 1fr; } }
.pie-wrap { display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; }
.pie { width: 150px; height: 150px; border-radius: 50%; position: relative; background: conic-gradient(var(--green) 0 __SAFE_DEG__deg, var(--orange) __SAFE_DEG__deg 360deg); }
.pie::after { content: ''; position: absolute; inset: 25%; background: white; border-radius: 50%; }
.pie-legend { font-size: .85rem; }
.pie-legend .row { display: flex; align-items: center; gap: .5rem; margin-bottom: .3rem; }
.dot { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.bars { display: flex; align-items: flex-end; gap: 6px; height: 120px; margin-top: .8rem; }
.bar-col { flex: 1; text-align: center; font-size: .7rem; color: var(--muted); }
.bar { background: linear-gradient(to top, #0366d6, #4a90d9); border-radius: 4px 4px 0 0; min-height: 2px; margin: 0 auto; width: 80%; }
.bar-label { margin-top: 4px; }
table { width: 100%; border-collapse: collapse; font-size: .85rem; }
th, td { text-align: left; padding: .5rem .4rem; border-bottom: 1px solid #eee; }
.status-pill { display: inline-block; padding: 2px 10px; border-radius: 20px; font-size: .75rem; font-weight: 600; }
.status-open { background: #fff3cd; color: #856404; }
.status-closed { background: #e8f5e9; color: #0a9e5e; }
.profit { color: var(--green); font-weight: 600; }
.loss { color: #c62828; font-weight: 600; }
.upgrade-chip { display: inline-block; background: #e8f0fe; color: var(--blue); border-radius: 8px; padding: .4rem .8rem; margin: .2rem .3rem .2rem 0; font-size: .82rem; }
.tip { background: #e8f5e9; border-left: 4px solid var(--green); border-radius: 8px; padding: .7rem 1rem; margin-bottom: .6rem; font-size: .88rem; }
.muted { color: var(--muted); font-size: .85rem; }
</style>
</head>
<body>
<header>
  <h1>🤖 Money Machine — Dashboard</h1>
  <p>__SITE_NAME__ · nicchia: __NICHE__ · aggiornato il __DATE__</p>
</header>
<main>
  <section class="cards">
    <div class="card"><div class="label">Guadagnato in totale</div><div class="value">__TOTAL__</div><div class="sub">da quando esiste</div></div>
    <div class="card green"><div class="label">🛡️ SAFE (imbattibile)</div><div class="value">__SAFE__</div><div class="sub">non si tocca mai</div></div>
    <div class="card orange"><div class="label">⚡ RISK budget</div><div class="value">__RISK__</div><div class="sub">per le campagne</div></div>
    <div class="card blue"><div class="label">Articoli pubblicati</div><div class="value">__POSTS__</div><div class="sub">sul sito</div></div>
  </section>

  <div class="two-col">
    <div class="panel">
      <h2>📊 Dov'è il denaro</h2>
      <div class="pie-wrap">
        <div class="pie"></div>
        <div class="pie-legend">
          <div class="row"><span class="dot" style="background:var(--green)"></span>SAFE __SAFE__ ( __SAFE_PCT__% )</div>
          <div class="row"><span class="dot" style="background:var(--orange)"></span>RISK __RISK__ ( __RISK_PCT__% )</div>
        </div>
      </div>
    </div>
    <div class="panel">
      <h2>📈 Entrate per mese</h2>
      <div class="bars">__BARS__</div>
    </div>
  </div>

  <div class="panel">
    <h2>🔧 Upgrade</h2>
    __UP_CHIPS__
  </div>

  <div class="panel">
    <h2>📋 Campagne rischio</h2>
    <table>
      <thead><tr><th>Campagna</th><th>Tipo</th><th>Investito</th><th>Ritornato</th><th>Profitto</th><th>Stato</th></tr></thead>
      <tbody>__CAMP_ROWS__</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>💡 Consigli per guadagnare di più</h2>
    __TIPS_HTML__
  </div>
</main>
</body>
</html>"""

    html = (html
            .replace("__SITE_NAME__", cfg["site_name"])
            .replace("__NICHE__", cfg["niche"])
            .replace("__DATE__", dt.datetime.now().strftime("%d %B %Y"))
            .replace("__TOTAL__", fmt(total))
            .replace("__SAFE__", fmt(safe))
            .replace("__RISK__", fmt(risk))
            .replace("__POSTS__", str(posts))
            .replace("__SAFE_PCT__", str(safe_pct))
            .replace("__RISK_PCT__", str(risk_pct))
            .replace("__SAFE_DEG__", str(safe_pct * 3.6))
            .replace("__BARS__", bars_html)
            .replace("__UP_CHIPS__", up_chips)
            .replace("__CAMP_ROWS__", camp_rows_html)
            .replace("__TIPS_HTML__", tips_html))

    path = os.path.join(SITEDIR, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ============================================================
# DASHBOARD — server interattivo
# ============================================================

def _count_posts():
    if not os.path.isdir(POSTSDIR):
        return 0
    return len([f for f in os.listdir(POSTSDIR) if f.endswith(".html")])


def serve_dashboard(cfg, port=8000):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.parse as up

    tpl_path = os.path.join(WORKDIR, "templates", "dashboard.html")
    if not os.path.exists(tpl_path):
        raise SystemExit("template dashboard.html mancante in templates/")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode() if n else "{}"
            try:
                return json.loads(raw)
            except Exception:
                return {}

        def do_GET(self):
            path = up.urlparse(self.path).path
            if path == "/api/status":
                led = fin.load_ledger()
                stats = {
                    "site_name": cfg.get("site_name"),
                    "niche": cfg.get("niche"),
                    "total": led["total_earned"],
                    "safe": led["safe_balance"],
                    "risk": led["risk_balance"],
                    "posts": _count_posts(),
                    "upgrades": led["upgrades_applied"],
                    "upgrades_available": fin.list_upgrades(cfg),
                    "campaigns": led["campaigns"],
                    "history": led["history"],
                    "tips": cfg.get("tips", []),
                }
                self._send(200, json.dumps(stats))
                return
            if path in ("/", "/dashboard"):
                # Versione statica con dati incorporati — funziona anche
                # in preview sandboxato (nessuna fetch necessaria)
                static_path = export_dashboard(cfg)
                with open(static_path, encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
                return
            if path in ("/interactive", "/interactive.html"):
                with open(tpl_path, encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
                return
            # static files from site/
            rel = path.lstrip("/")
            fp = os.path.join(SITEDIR, rel)
            if os.path.isfile(fp):
                ctype = "text/html; charset=utf-8" if fp.endswith(".html") else "application/octet-stream"
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ctype)
                return
            self._send(404, json.dumps({"ok": False, "msg": "not found"}))

        def do_POST(self):
            path = up.urlparse(self.path).path
            body = self._read_json()
            led = fin.load_ledger()
            try:
                if path == "/api/income":
                    safe, risk = fin.report_earning(led, cfg, body.get("amount", 0), body.get("source", "AdSense"))
                    fin.save_ledger(led)
                    self._send(200, json.dumps({"ok": True, "safe": safe, "risk": risk}))
                elif path == "/api/invest":
                    ok, msg = fin.invest(led, cfg, body.get("campaign", ""), body.get("amount", 0), body.get("note", ""))
                    cid = None
                    if ok:
                        cid = list(led["campaigns"].keys())[-1]
                        fin.save_ledger(led)
                    self._send(200, json.dumps({"ok": ok, "msg": msg, "campaign_id": cid}))
                elif path == "/api/settle":
                    ok, msg = fin.settle(led, cfg, body.get("campaign_id", ""), body.get("return_amount", 0))
                    fin.save_ledger(led)
                    self._send(200, json.dumps({"ok": ok, "msg": msg}))
                elif path == "/api/upgrade":
                    ok, msg = fin.apply_upgrade(led, cfg, body.get("name", ""))
                    fin.save_ledger(led)
                    self._send(200, json.dumps({"ok": ok, "msg": msg}))
                else:
                    self._send(404, json.dumps({"ok": False, "msg": "endpoint not found"}))
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "msg": str(e)}))

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log(f"dashboard su http://0.0.0.0:{port}")
    log("endpoint: /api/status · /api/income · /api/invest · /api/settle · /api/upgrade")
    srv.serve_forever()


# ============================================================
# LOOP — esecuzione continua
# ============================================================

def run_loop(cfg, interval_hours, max_articles, mode):
    log(f"loop attivato: {interval_hours}h, max {max_articles} articoli")
    published = 0
    while True:
        topics = discover_topics(cfg, count=3)
        if topics:
            topic = topics[0]
            log(f"topic selezionato: [{topic['type']}] {topic['topic']}")
            try:
                path = publish_topic(cfg, topic, mode, auto=cfg.get("auto_publish", False))
                published += 1
                save_posts_index()
                # controllo auto-upgrade (se safe supera soglie, spende da solo)
                fin.auto_upgrade_check(fin.load_ledger(), cfg, silent=False)
                if published >= max_articles:
                    log(f"published {published} articoli. Fermo.")
                    return
            except Exception as e:
                log(f"errore nel ciclo: {e}")
        log(f"dormo {interval_hours}h...")
        time.sleep(interval_hours * 3600)


# ============================================================
# COMMAND
# ============================================================

def log(msg):
    print(f"[money-machine] {msg}")


def main():
    ap = argparse.ArgumentParser(description="Money Machine — articolo automatico monetizzato")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("discover", help="mostra topic reali (trend + news)")
    sp.add_argument("--count", type=int, default=5)
    sp.add_argument("--mode", default="auto", choices=["auto", "ollama", "hf", "offline"])

    sp = sub.add_parser("publish", help="genera e pubblica un articolo")
    sp.add_argument("topic", help="tema dell'articolo")
    sp.add_argument("--news", type=int, default=8, help="numero fonti news")
    sp.add_argument("--auto", action="store_true", help="pubblica senza review")
    sp.add_argument("--mode", default="auto", choices=["auto", "ollama", "hf", "offline"])

    sp = sub.add_parser("review", help="pubblica una bozza esistente")
    sp.add_argument("path")

    sp = sub.add_parser("loop", help="modalità continua")
    sp.add_argument("--interval", type=float, default=24, help="ore tra un articolo e l'altro")
    sp.add_argument("--max", type=int, default=30)
    sp.add_argument("--mode", default="auto", choices=["auto", "ollama", "hf", "offline"])

    sp = sub.add_parser("serve", help="server preview locale")
    sp.add_argument("--port", type=int, default=8000)

    sp = sub.add_parser("setup", help="crea il sito base")
    sp.add_argument("--force", action="store_true")

    sp = sub.add_parser("income", help="registra un'entrata (AdSense/affiliate)")
    sp.add_argument("amount", type=float)
    sp.add_argument("--source", default="AdSense")

    sp = sub.add_parser("upgrade", help="applica un upgrade pagato")
    sp.add_argument("name")

    sp = sub.add_parser("invest", help="investe dal risk budget in una campagna")
    sp.add_argument("campaign")
    sp.add_argument("amount", type=float)
    sp.add_argument("--note", default="")

    sp = sub.add_parser("settle", help="chiude una campagna rischio")
    sp.add_argument("campaign_id")
    sp.add_argument("return_amount", type=float)

    sp = sub.add_parser("finance", help="mostra lo stato finanziario")

    sp = sub.add_parser("dashboard", help="launcha il dashboard interattivo")
    sp.add_argument("--port", type=int, default=8000)

    sp = sub.add_parser("export-dashboard", help="genera dashboard statico in site/")
    sp.add_argument("--port", type=int, default=8000)

    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "export-dashboard":
        path = export_dashboard(cfg)
        print(f"✔ dashboard statico generato: {path}")
        print(f"  aprilo con: python3 -m http.server 8000 --directory {SITEDIR}")
        print(f"  o chiama: python3 engine.py serve --port 8000")
        return

    if args.cmd == "dashboard":
        serve_dashboard(cfg, port=args.port)
        return

    if args.cmd == "finance":
        led = fin.load_ledger()
        print(fin.status(led, cfg))
        return

    if args.cmd == "income":
        led = fin.load_ledger()
        safe, risk = fin.report_earning(led, cfg, args.amount, args.source)
        print(f"✔ entrata registrata: {args.amount:.2f}€ da {args.source}")
        print(f"  → safe: {safe:.2f}€ | risk: {risk:.2f}€")
        print(f"  saldo: safe {led['safe_balance']:.2f}€ · risk {led['risk_balance']:.2f}€")
        return

    if args.cmd == "upgrade":
        led = fin.load_ledger()
        ok, msg = fin.apply_upgrade(led, cfg, args.name)
        log(msg)
        fin.save_ledger(led)
        return

    if args.cmd == "invest":
        led = fin.load_ledger()
        ok, msg = fin.invest(led, cfg, args.campaign, args.amount, args.note)
        log(msg)
        if "prediction" in args.campaign:
            log("⚠️  AVVERTENZA: questo è azzardo a aspettativa negativa.")
            log("   I bot su Kalshi hanno perso -16% a -31%. Non è un investimento.")
        fin.save_ledger(led)
        return

    if args.cmd == "settle":
        led = fin.load_ledger()
        ok, msg = fin.settle(led, cfg, args.campaign_id, args.return_amount)
        log(msg)
        print(fin.status(led, cfg))
        return

    if args.cmd == "setup":
        setup_site(cfg, force=args.force)
        return

    if args.cmd == "serve":
        log(f"server su 0.0.0.0:{args.port}")
        subprocess.call([sys.executable, "-m", "http.server", str(args.port), "--directory", SITEDIR])
        return

    if args.cmd == "discover":
        for t in discover_topics(cfg, count=args.count):
            print(f"\n[{t['type']}] {t['topic']}"
                  + (f" (traffico: {t.get('traffic')})" if t.get("traffic") else ""))
            for n in t.get("news", [])[:4]:
                print(f"   - {n['title']} — {n.get('source', '')}")
        return

    if args.cmd == "review":
        run_review(args.path)
        save_posts_index()
        return

    if args.cmd == "loop":
        setup_site(cfg)
        run_loop(cfg, args.interval, args.max, args.mode)
        return

    if args.cmd == "publish":
        setup_site(cfg)
        try:
            news = fetch_news(args.topic, n=args.news)
        except Exception as e:
            log(f"news fallite ({e}), uso trend")
            news = []
        topic = {"topic": args.topic, "news": news, "type": "manual"}
        publish_topic(cfg, topic, args.mode, args.auto)
        save_posts_index()
        return


if __name__ == "__main__":
    main()
