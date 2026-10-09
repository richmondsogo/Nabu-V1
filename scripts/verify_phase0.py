import httpx
import sys

MD5 = "7a7ef891b9d2b2ae8d9cd864556f7cd8"

urls = [
    ("jina_is_md5", f"https://r.jina.ai/https://libgen.is/md5/{MD5}"),
    ("jina_is_book_index", f"https://r.jina.ai/https://libgen.is/book/index.php?md5={MD5}"),
    ("jina_rs_md5", f"https://r.jina.ai/https://libgen.rs/md5/{MD5}"),
    ("allorigins_is_md5", f"https://api.allorigins.win/raw?url=https://libgen.is/md5/{MD5}"),
    ("jina_la_ads", f"https://r.jina.ai/https://libgen.la/ads.php?md5={MD5}"),
    ("jina_bz_ads", f"https://r.jina.ai/https://libgen.bz/ads.php?md5={MD5}"),
    # Direct tests just in case .la or .bz are directly reachable from here
    ("direct_la_ads", f"https://libgen.la/ads.php?md5={MD5}"),
    ("direct_bz_ads", f"https://libgen.bz/ads.php?md5={MD5}"),
]

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

results = []
with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
    for label, url in urls:
        try:
            r = client.get(url)
            snippet = r.text[:150].replace("\n", " ").strip()
            print(f"[{label}] Status: {r.status_code} | Length: {len(r.content)} | Snippet: {snippet[:80]}")
            results.append((label, r.status_code, len(r.content), snippet))
        except Exception as e:
            print(f"[{label}] ERROR: {type(e).__name__}: {e}")
            results.append((label, "ERROR", 0, str(e)))

print("\n--- Summary ---")
for label, status, length, snippet in results:
    print(f"{label:20} -> {status} ({length} bytes)")
