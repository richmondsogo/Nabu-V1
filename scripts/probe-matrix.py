"""Comprehensive UA-matrix and IPFS Gateway matrix probe.

Settles:
1. UA-binding & Key determinism on libgen.li:
   - Same UA repeated 5x
   - Key generated with UA1 requested with UA1 vs UA2
   - Key generated with Mobile Safari requested with Mobile Safari
2. Expanded IPFS Gateway Matrix:
   - Path and subdomain formats across dweb.link, w3s.link, nftstorage.link,
     gateway.pinata.cloud, 4everland.io, flk-ipfs.xyz, trustless-gateway.link, etc.
"""

import re
import sys
import time
import httpx

MD5_SAMPLE = "7a7ef891b9d2b2ae8d9cd864556f7cd8"

UA_DESKTOP_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
UA_DESKTOP_FIREFOX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
)
UA_MOBILE_SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)
UA_MOBILE_CHROME = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
)

UAS = {
    "Desktop Chrome": UA_DESKTOP_CHROME,
    "Desktop Firefox": UA_DESKTOP_FIREFOX,
    "iPhone Safari": UA_MOBILE_SAFARI,
    "Android Chrome": UA_MOBILE_CHROME,
}

CIDS = [
    "bafykbzaced572s64zfvsmv7kld47e25ptz7evydr7hhy4hgbq77o4qjty4d7q",
    "bafykbzaced4jdb5n22i3phq5w6spcqoqm43o4tftxeq32pmrqxuxzce2a677y",
]


def fetch_key_with_ua(ua: str) -> str | None:
    time.sleep(1.0)  # Polite delay
    url = f"https://libgen.li/ads.php?md5={MD5_SAMPLE}"
    with httpx.Client(timeout=15.0, headers={"User-Agent": ua}) as client:
        r = client.get(url)
        if r.status_code != 200:
            print(f"  [ads.php] Returned {r.status_code} for UA")
            return None
        m = re.search(r'href=["\'](get\.php\?md5=[^"\']+)["\']', r.text)
        if m:
            return f"https://libgen.li/{m.group(1)}"
    return None


def run_ua_matrix():
    print("=" * 70)
    print(" 1. LIBGEN.LI UA-BINDING & KEY REPEATABILITY MATRIX")
    print("=" * 70)

    # Test 1A: Key generated with Desktop Chrome
    print("\n--- Test 1A: Key generated with Desktop Chrome ---")
    chrome_url = fetch_key_with_ua(UA_DESKTOP_CHROME)
    if not chrome_url:
        print("Failed to fetch key with Desktop Chrome.")
        return
    print(f"Generated URL: {chrome_url}")

    # Repeat 5x with Desktop Chrome (check determinism)
    print("\nRepeating 5x with matching UA (Desktop Chrome):")
    for i in range(1, 6):
        time.sleep(0.5)
        with httpx.Client(timeout=10.0, headers={"User-Agent": UA_DESKTOP_CHROME}) as c:
            r = c.get(chrome_url)
            print(f"  Run {i}/5: Status {r.status_code} | Bytes: {len(r.content)}")

    # Request the Chrome-generated key with other UAs
    print("\nRequesting the SAME Chrome-generated key with different UAs:")
    for name, ua in UAS.items():
        if name == "Desktop Chrome":
            continue
        time.sleep(0.5)
        with httpx.Client(timeout=10.0, headers={"User-Agent": ua}) as c:
            try:
                r = c.get(chrome_url)
                print(f"  {name:16}: Status {r.status_code} | Bytes: {len(r.content)}")
            except Exception as e:
                print(f"  {name:16}: Error ({type(e).__name__}: {e})")

    # Test 1B: Key generated with iPhone Safari
    print("\n--- Test 1B: Key generated with iPhone Safari ---")
    safari_url = fetch_key_with_ua(UA_MOBILE_SAFARI)
    if not safari_url:
        print("Failed to fetch key with iPhone Safari.")
    else:
        print(f"Generated URL: {safari_url}")
        print("Requesting with iPhone Safari:")
        with httpx.Client(timeout=10.0, headers={"User-Agent": UA_MOBILE_SAFARI}) as c:
            r = c.get(safari_url)
            print(f"  iPhone Safari (native): Status {r.status_code} | Bytes: {len(r.content)}")

        print("Requesting iPhone Safari key with Desktop Chrome:")
        with httpx.Client(timeout=10.0, headers={"User-Agent": UA_DESKTOP_CHROME}) as c:
            r = c.get(safari_url)
            print(f"  Desktop Chrome (cross): Status {r.status_code} | Bytes: {len(r.content)}")


def run_gateway_matrix():
    print("\n" + "=" * 70)
    print(" 2. EXPANDED IPFS GATEWAY MATRIX (Path & Subdomain)")
    print("=" * 70)

    gateways = [
        # (name, path_template, subdomain_template)
        ("ipfs.io", "https://ipfs.io/ipfs/{cid}", "https://{cid}.ipfs.ipfs.io"),
        ("dweb.link", "https://dweb.link/ipfs/{cid}", "https://{cid}.ipfs.dweb.link"),
        ("w3s.link", "https://w3s.link/ipfs/{cid}", "https://{cid}.ipfs.w3s.link"),
        ("nftstorage.link", "https://nftstorage.link/ipfs/{cid}", "https://{cid}.ipfs.nftstorage.link"),
        ("pinata", "https://gateway.pinata.cloud/ipfs/{cid}", None),
        ("4everland", "https://4everland.io/ipfs/{cid}", "https://{cid}.ipfs.4everland.io"),
        ("flk-ipfs", "https://flk-ipfs.xyz/ipfs/{cid}", "https://{cid}.ipfs.flk-ipfs.xyz"),
        ("trustless-gateway", "https://trustless-gateway.link/ipfs/{cid}", None),
    ]

    cid = CIDS[0]
    print(f"Testing with known Libgen CID: {cid}")

    for name, path_tpl, sub_tpl in gateways:
        # 1. Path format
        if path_tpl:
            url = path_tpl.format(cid=cid)
            try:
                t0 = time.time()
                with httpx.Client(timeout=6.0, headers={"User-Agent": UA_DESKTOP_CHROME, "Range": "bytes=0-511"}) as c:
                    r = c.get(url)
                    dt = time.time() - t0
                    print(f"  [PATH]      {name:18} -> Status {r.status_code} in {dt:.2f}s | Content-Type: {r.headers.get('content-type', 'none')[:30]}")
            except httpx.TimeoutException:
                print(f"  [PATH]      {name:18} -> TIMEOUT (>6s)")
            except Exception as e:
                print(f"  [PATH]      {name:18} -> {type(e).__name__}: {str(e)[:40]}")

        # 2. Subdomain format
        if sub_tpl:
            url = sub_tpl.format(cid=cid)
            try:
                t0 = time.time()
                with httpx.Client(timeout=6.0, headers={"User-Agent": UA_DESKTOP_CHROME, "Range": "bytes=0-511"}) as c:
                    r = c.get(url)
                    dt = time.time() - t0
                    print(f"  [SUBDOMAIN] {name:18} -> Status {r.status_code} in {dt:.2f}s | Content-Type: {r.headers.get('content-type', 'none')[:30]}")
            except httpx.TimeoutException:
                print(f"  [SUBDOMAIN] {name:18} -> TIMEOUT (>6s)")
            except Exception as e:
                print(f"  [SUBDOMAIN] {name:18} -> {type(e).__name__}: {str(e)[:40]}")


if __name__ == "__main__":
    run_ua_matrix()
    run_gateway_matrix()
