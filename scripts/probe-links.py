"""Phase 0 Link Probes for Nabu Link Resolver Architecture.

Probes the 4 load-bearing architectural questions:
1. Does https://libgen.li/get.php?md5=<md5> work standalone, with no &key= and no cookies?
2. Does a resolved get.php?md5=...&key=... URL work with no cookies, without Referer, and across time/IP?
3. Does https://libgen.li/ads.php?md5=<md5> render for an anonymous user?
4. Does https://ipfs.io/ipfs/<cid> resolve for real book CIDs?
"""

import sys
import time
import httpx

MD5_SAMPLE = "7a7ef891b9d2b2ae8d9cd864556f7cd8"  # "The Rust Programming Language"
# Known CIDs from Libgen / Anna's Archive IPFS dumps
KNOWN_CIDS = [
    "bafykbzaced572s64zfvsmv7kld47e25ptz7evydr7hhy4hgbq77o4qjty4d7q",
    "bafykbzaced4jdb5n22i3phq5w6spcqoqm43o4tftxeq32pmrqxuxzce2a677y",
    "bafykbzacec2w5w322j4qgy626a5vhcldj5f45zlyj7j65i257i6qlyz3h2uqa",
    "QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco", # Canonical IPFS test CID
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def log_section(title: str):
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70)

def probe_1_standalone_get():
    log_section("PROBE 1: Does https://libgen.li/get.php?md5=<md5> work standalone (no key, no cookies)?")
    url = f"https://libgen.li/get.php?md5={MD5_SAMPLE}"
    print(f"Requesting: {url} (fresh client, cookies=None, referer=None)")
    
    with httpx.Client(timeout=15.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        try:
            r = client.get(url)
            print(f"Response Status: {r.status_code}")
            print(f"Response Headers:")
            for k, v in r.headers.items():
                if k.lower() in ("location", "content-type", "content-length", "server", "set-cookie"):
                    print(f"  {k}: {v}")
            print(f"Body snippet (first 300 chars):")
            print(repr(r.text[:300]))

            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                print(f"Redirect target: {loc}")
                r_followed = client.get(r.headers["location"])
                print(f"After following redirect -> Status: {r_followed.status_code}, Length: {len(r_followed.content)}")
                print(f"Snippet: {repr(r_followed.text[:200])}")
        except Exception as e:
            print(f"Request failed: {type(e).__name__}: {e}")

def probe_2_resolved_key_durability():
    log_section("PROBE 2: Does resolved get.php?md5=...&key=... work (cookies, referer, expiry)?")
    # Step A: obtain a fresh key from ads.php
    ads_url = f"https://libgen.li/ads.php?md5={MD5_SAMPLE}"
    print(f"Step A: Fetching fresh key from: {ads_url}")
    resolved_url = None
    with httpx.Client(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        r_ads = client.get(ads_url)
        import re
        match = re.search(r'href=["\'](get\.php\?md5=[^"\']+)["\']', r_ads.text)
        if match:
            rel_url = match.group(1)
            resolved_url = f"https://libgen.li/{rel_url}"
            print(f"Discovered key URL: {resolved_url}")
        else:
            print("Could not find get.php link on ads.php")
            return

    # Step B: test with NO cookies and NO referer header
    print("\nTest 2b: Requesting resolved URL with NO cookies and NO Referer:")
    with httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        try:
            r_stream = client.get(resolved_url)
            print(f"Status: {r_stream.status_code}")
            print(f"Content-Type: {r_stream.headers.get('content-type')}")
            print(f"Content-Length: {r_stream.headers.get('content-length')}")
            print(f"Bytes received: {len(r_stream.content)}")
            print(f"First 16 bytes: {repr(r_stream.content[:16])}")
        except Exception as e:
            print(f"Failed: {type(e).__name__}: {e}")

    # Step C: inspect key structure and IP binding analysis
    print("\nTest 2c: Key parameter inspection & IP binding probe:")
    key_val = re.search(r'key=([A-Za-z0-9]+)', resolved_url)
    if key_val:
        k = key_val.group(1)
        print(f"Key value: {k} (length: {len(k)})")

    # Step D: Test with different User-Agents
    print("\nTest 2d: Requesting same key with different User-Agent (curl / mobile):")
    for ua_name, ua_str in [("curl/8.4.0", "curl/8.4.0"), ("iPhone Safari", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")]:
        with httpx.Client(timeout=15.0, headers={"User-Agent": ua_str}) as client:
            try:
                r_ua = client.get(resolved_url)
                print(f"  UA '{ua_name}' -> Status: {r_ua.status_code}, Bytes: {len(r_ua.content)}")
            except Exception as e:
                print(f"  UA '{ua_name}' -> Failed: {e}")

def probe_3_anonymous_ads_page():
    log_section("PROBE 3: Does https://libgen.li/ads.php?md5=<md5> render for an anonymous user?")
    url = f"https://libgen.li/ads.php?md5={MD5_SAMPLE}"
    print(f"Requesting: {url} (completely anonymous, fresh client)")
    
    with httpx.Client(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
        try:
            r = client.get(url)
            print(f"Response Status: {r.status_code}")
            print(f"Response Length: {len(r.text)} characters")
            import re
            title_match = re.search(r'<title>(.*?)</title>', r.text, re.IGNORECASE)
            print(f"Page Title: {title_match.group(1) if title_match else 'None'}")
            has_get = bool(re.search(r'get\.php\?md5=', r.text))
            print(f"Contains direct get.php download link: {has_get}")
            # Check for Cloudflare / bot challenges
            is_cf_blocked = "cf-browser-verification" in r.text or "challenge-platform" in r.text
            print(f"Cloudflare Turnstile / Challenge detected: {is_cf_blocked}")
        except Exception as e:
            print(f"Failed: {type(e).__name__}: {e}")

def probe_4_public_ipfs_gateways():
    log_section("PROBE 4: Does https://ipfs.io/ipfs/<cid> resolve for real book CIDs?")
    gateways = [
        "https://ipfs.io/ipfs",
        "https://dweb.link/ipfs",
        "https://cloudflare-ipfs.com/ipfs",
        "https://gateway.pinata.cloud/ipfs",
    ]

    for cid in KNOWN_CIDS:
        print(f"\nTesting CID: {cid}")
        for gw in gateways:
            gw_url = f"{gw}/{cid}"
            try:
                t0 = time.time()
                # Use HEAD or range GET (first 1024 bytes) to avoid downloading large files
                with httpx.Client(timeout=8.0, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-1023"}) as client:
                    r = client.get(gw_url)
                    dt = time.time() - t0
                    print(f"  {gw_url:65} -> Status: {r.status_code} in {dt:.2f}s | Content-Type: {r.headers.get('content-type', 'none')}")
            except httpx.TimeoutException:
                print(f"  {gw_url:65} -> TIMEOUT (>8s)")
            except Exception as e:
                print(f"  {gw_url:65} -> ERROR: {type(e).__name__}: {e}")

if __name__ == "__main__":
    probe_1_standalone_get()
    probe_2_resolved_key_durability()
    probe_3_anonymous_ads_page()
    probe_4_public_ipfs_gateways()
