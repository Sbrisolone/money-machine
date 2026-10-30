#!/usr/bin/env python3
"""
Script per GitHub Actions: esegue un solo ciclo di pubblicazionearticolo.
Viene chiamato dal workflow .github/workflows/loop.yml ogni giorno.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine

if __name__ == "__main__":
    cfg = engine.load_config()
    engine.setup_site(cfg)
    topics = engine.discover_topics(cfg, count=3)
    if not topics:
        print("[github-run] Nessun topic disponibile, fine.")
        sys.exit(0)
    topic = topics[0]
    print(f"[github-run] topic: {topic['topic']}")
    engine.publish_topic(cfg, topic, "offline", auto=cfg.get("auto_publish", False))
    engine.save_posts_index()
    print("[github-run] ciclo completato.")
