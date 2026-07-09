"""
agent_embed.py — feeds fresh weekly coding activity into the EXISTING Upstash
Vector knowledge base that chat.js already retrieves from. No new RAG, no chat.js
change: chat.js's hybrid retrieval + reranker surface this chunk automatically.

Design notes:
  • Uses a STABLE vector id ('agent-ctx-coding-live') so each weekly run OVERWRITES
    the previous chunk instead of piling up stale copies (no orphan cleanup needed).
  • Embeds with text-embedding-3-small (1536-dim) to MATCH chat.js's query embedding
    (api/chat.js line ~1230) — vectors must be same model/dims to be comparable.
  • Metadata uses { text } to match what chat.js reads (r.metadata?.text).
  • Fully non-fatal: any missing cred / API hiccup just skips (chat keeps working).

Secrets: OPENAI_API_KEY, UPSTASH_VECTOR_REST_URL, UPSTASH_VECTOR_REST_TOKEN.
Reads agent_context_gold.json (written by agent_context_pipeline.py). Stdlib only.
"""

import json, os, urllib.request
from pathlib import Path
from datetime import datetime, timezone

GOLD_PATH = Path(__file__).parent.parent / "agent_context_gold.json"
EMBED_MODEL = "text-embedding-3-small"
CHUNK_ID = "agent-ctx-coding-live"          # stable → weekly overwrite


def _post_json(url, body, headers, timeout=45):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def embed_text(text, api_key, post=_post_json):
    data = post("https://api.openai.com/v1/embeddings",
                {"model": EMBED_MODEL, "input": text},
                {"Authorization": f"Bearer {api_key}"})
    return data["data"][0]["embedding"]


def build_chunk(coding, now):
    repos = ", ".join(f"{r['name']} ({r['commits']} commits)" for r in coding.get("active_repos", [])) \
            or "no public repositories this week"
    return (f"Yash's recent coding activity (auto-updated weekly, as of {now.date().isoformat()}): "
            f"{coding.get('summary', '').strip()} Active repositories this week: {repos}. "
            f"This reflects what Yash is currently building.")


def upsert_vector(vec_url, vec_token, vector, text, now, post=_post_json):
    return post(vec_url.rstrip("/") + "/upsert",
                {"id": CHUNK_ID, "vector": vector,
                 "metadata": {"text": text, "source": "agent-context", "updated_at": now.isoformat()}},
                {"Authorization": f"Bearer {vec_token}"})


def main(post=_post_json):
    now = datetime.now(timezone.utc)
    openai_key = os.environ.get("OPENAI_API_KEY")
    vec_url    = os.environ.get("UPSTASH_VECTOR_REST_URL")
    vec_token  = os.environ.get("UPSTASH_VECTOR_REST_TOKEN")
    if not (openai_key and vec_url and vec_token):
        print("Embed skipped — missing OPENAI_API_KEY / UPSTASH_VECTOR_* (RAG keeps last-good chunk).")
        return
    try:
        gold = json.loads(GOLD_PATH.read_text())
    except Exception as e:
        print(f"No agent_context_gold.json to embed ({e}) — skipping.")
        return

    chunk = build_chunk(gold.get("coding", {}), now)
    try:
        vector = embed_text(chunk, openai_key, post=post)
        if not isinstance(vector, list) or len(vector) < 100:
            print("Unexpected embedding shape — skipping upsert."); return
        upsert_vector(vec_url, vec_token, vector, chunk, now, post=post)
        print(f"OK embedded coding chunk ({len(vector)}-dim) -> vector id '{CHUNK_ID}'")
        print(f"  chunk: {chunk[:120]}...")
    except Exception as e:
        print(f"Embed/upsert failed ({e}) — non-fatal, RAG keeps last-good chunk.")


if __name__ == "__main__":
    main()
